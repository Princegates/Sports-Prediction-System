"""Unauthenticated endpoints for the public marketing site.

Everything else in this API is gated behind an approved account. The welcome
page still needs real numbers on it -- a landing page that claims "thousands of
matches analyzed" without reading the database is the kind of thing that turns
out to be false the moment someone checks.

So these endpoints exist, and the rule for what may live here is strict: the
corpus size, the model's own measured accuracy, and the *shape* of an upcoming
fixture list. No probabilities, no selections, no reasoning -- those are the
product, and they stay behind the login.

One deliberate exception: public_teaser_pick. An admin can choose to show one
real, priced combo's actual teams/market/selection here -- a marketing
teaser, not an accident. See its own docstring for why it's carved out of
the rule above rather than being the rule.
"""

from __future__ import annotations

import datetime as dt
import hashlib

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.timeutil import utcnow
from app.api.deps import get_db
from app.api.schemas import (
    AdminPickLegOut,
    AdminPickTrackRecordOut,
    BrandingOut,
    ConfidenceBandRecordOut,
    LeagueAccuracyOut,
    LandingOut,
    NoticeOut,
    PublicFreePickOut,
    WinningSlipOut,
    PublicAccuracyOut,
    PublicFixtureDetailOut,
    PublicFixtureOut,
    PublicHeadToHeadOut,
    PublicResultOut,
    PublicStatsOut,
    PublicTeaserPickOut,
    TrackRecordEntryOut,
    TrackRecordOut,
)
from app import app_settings
from app.api.serializers import TEAM_LOGO_URL, public_teaser_pick_to_schema
from app.assistant import retrieval
from app.betcode.selection import Leg, price_legs
from app.db.models import AdminPick, FeaturedPick, Match, Prediction, PublicFreePickSet, User, WinningSlip
from app.winning_slips import winning_slip_to_schema
from app.outcomes.grading import grade_outcome
from app.outcomes.registry import full_registry_market_count
from app.pick_settlement import frozen_admin_pick_legs, settle_admin_pick, settle_featured_pick

router = APIRouter(prefix="/api/public", tags=["public"])

# Shared by the list and detail fixture endpoints: how far ahead "upcoming"
# reaches. The detail endpoint 404s outside this window on purpose -- a
# match-preview page (built for SEO -- see /fixtures/{match_id}'s own
# docstring) has no reason to exist for a fixture nobody can search for yet,
# or one that's already kicked off.
MAX_PUBLIC_FIXTURE_DAYS_AHEAD = 14

# How many of the most recent settled, featured picks admin_pick_track_record
# shows in full -- the aggregate counts it also returns always cover every
# one regardless of this cap, so this only bounds the detail list's payload
# size on a page that otherwise grows forever.
MAX_TRACK_RECORD_ENTRIES = 25


def _public_fixture_out(db: Session, match: Match) -> PublicFixtureOut:
    prediction = db.execute(
        select(Prediction)
        .where(Prediction.match_id == match.id)
        .order_by(Prediction.created_at.desc())
        .limit(1)
    ).scalars().first()
    return PublicFixtureOut(
        match_id=match.id,
        league=match.league,
        kickoff=match.date,
        home_team=match.home_team.name,
        away_team=match.away_team.name,
        has_prediction=prediction is not None,
        confidence=prediction.confidence if prediction else None,
        home_logo_url=_logo(match.home_team.api_football_team_id),
        away_logo_url=_logo(match.away_team.api_football_team_id),
    )


def _logo(provider_id: int | None) -> str | None:
    return TEAM_LOGO_URL.format(id=provider_id) if provider_id else None


_CONFIDENCE_RANK = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


@router.get("/stats", response_model=PublicStatsOut)
def public_stats(db: Session = Depends(get_db)) -> PublicStatsOut:
    """Corpus-size figures, read live so the landing page can't drift out of
    sync with what the system actually holds."""

    counts = retrieval.coverage_counts(db)
    leagues = list(
        db.execute(select(Match.league).distinct().order_by(Match.league.asc())).scalars()
    )
    active_members = db.execute(
        select(func.count()).select_from(User).where(User.status == "active")
    ).scalar() or 0

    return PublicStatsOut(
        matches_analyzed=counts["matches_analyzed"],
        teams_tracked=counts["teams"],
        leagues_covered=counts["leagues"],
        predictions_generated=counts["predictions_generated"],
        upcoming_fixtures=counts["upcoming_fixtures"],
        active_members=active_members,
        league_names=leagues,
        # Computed from the registry itself (see full_registry_market_count's
        # own docstring) rather than a typed-in literal, which is exactly
        # what went stale here before -- the registry grew well past the old
        # hardcoded 18 and nothing caught it.
        markets_per_match=full_registry_market_count(),
    )


@router.get("/accuracy", response_model=PublicAccuracyOut)
def public_accuracy(db: Session = Depends(get_db)) -> PublicAccuracyOut:
    """The most recent backtest's held-out metrics.

    Returns ``has_data: false`` rather than placeholder figures when no
    backtest has been run -- the landing page then shows the methodology
    instead of a fabricated accuracy claim.
    """

    snapshot = retrieval.accuracy_snapshot(db)
    if not snapshot.has_data:
        return PublicAccuracyOut(has_data=False)

    preferred_split = (
        "test" if "test" in snapshot.splits
        else "validation" if "validation" in snapshot.splits
        else next(iter(snapshot.splits))
    )
    metrics = snapshot.splits[preferred_split]

    return PublicAccuracyOut(
        has_data=True,
        model_version=snapshot.model_version,
        computed_at=snapshot.computed_at,
        split=preferred_split,
        leagues=snapshot.leagues,
        accuracy=metrics.get("accuracy"),
        log_loss=metrics.get("log_loss"),
        brier_score=metrics.get("brier_score") or metrics.get("brier"),
        matches_evaluated=int(metrics["n"]) if "n" in metrics else (
            int(metrics["matches"]) if "matches" in metrics else None
        ),
        # The per-league spread is worth showing: it is real, and an overall
        # figure alone hides that some leagues are markedly harder to predict.
        per_league=[
            LeagueAccuracyOut(
                league=league,
                accuracy=values.get("accuracy"),
                matches_evaluated=int(values["n"]) if "n" in values else None,
            )
            for league, values in sorted(
                snapshot.per_league.items(),
                key=lambda kv: kv[1].get("accuracy") or 0,
                reverse=True,
            )
        ],
    )


@router.get("/track-record", response_model=TrackRecordOut)
def public_track_record(db: Session = Depends(get_db)) -> TrackRecordOut:
    """How the system's own real pre-match calls have actually done, graded
    against real final scores -- not the offline backtest in /accuracy, a
    live verifiable record. Answers "how did your calls actually do?" with
    something other than "trust us"."""

    record = retrieval.track_record(db)
    if not record.has_data:
        return TrackRecordOut(has_data=False)

    return TrackRecordOut(
        has_data=True,
        graded_predictions=record.graded_predictions,
        hit_rate=record.hit_rate,
        by_confidence=[
            ConfidenceBandRecordOut(confidence=b.confidence, graded=b.graded, hit_rate=b.hit_rate)
            for b in record.by_confidence
        ],
        since=record.earliest_graded_at,
    )


def _featured_pick_to_leg(db: Session, pick: FeaturedPick) -> Leg | None:
    """Resolves a settled FeaturedPick (Guda Pick) into the same Leg shape
    frozen_admin_pick_legs builds for AdminPick -- team names/league/kickoff
    come live from Match (permanent once played; FeaturedPick itself stores
    no snapshot of them), market/selection are the pick's own, probability
    is the frozen probability_at_pick snapshot taken when it was promoted.
    None if the match can no longer be found (never expected in practice)."""

    match = db.execute(
        select(Match).where(Match.id == pick.match_id).options(selectinload(Match.home_team), selectinload(Match.away_team))
    ).scalars().first()
    if match is None:
        return None
    return Leg(
        match_id=match.id, league=match.league, home_team=match.home_team.name, away_team=match.away_team.name,
        kickoff=match.date, market=pick.market, selection=pick.selection,
        model_probability=pick.probability_at_pick or 0.0, decimal_odds=None, priced_by=None,
        leg_result=pick.result if pick.result != "pending" else None,
    )


def _legs_to_schema(legs: list[Leg]) -> list[AdminPickLegOut]:
    return [
        AdminPickLegOut(
            match_id=leg.match_id, league=leg.league, home_team=leg.home_team, away_team=leg.away_team,
            kickoff=leg.kickoff, market=leg.market, selection=leg.selection, probability=leg.model_probability,
            decimal_odds=leg.decimal_odds, priced_by=leg.priced_by, result=leg.leg_result,
        )
        for leg in legs
    ]


@router.get("/admin-picks-track-record", response_model=AdminPickTrackRecordOut)
def admin_pick_track_record(db: Session = Depends(get_db)) -> AdminPickTrackRecordOut:
    """How the picks a Super Admin has chosen to showcase have actually
    done -- Admin Picks (multi-leg combos) and Guda Picks (single outcomes)
    combined into one record, since both are the same thing from a trust
    standpoint: a human choosing to put their name behind a call. A second,
    independent record alongside /track-record's model calls. Shows each
    featured, settled pick's real legs (see TrackRecordEntryOut for why --
    a deliberate exception to "stays behind the login"), capped to the most
    recent MAX_TRACK_RECORD_ENTRIES; the aggregate counts cover every
    featured settled pick regardless of that cap. Each pick type is counted
    only while its own enabled setting is on, and only for picks an admin
    committed to it before kickoff (featured_public_at) -- see
    app.track_record for why picks featured after their result don't count."""

    values = app_settings.all_values(db)
    settled: list[AdminPick | FeaturedPick] = []

    if values.get("admin_picks_enabled", True):
        settled.extend(
            db.execute(
                select(AdminPick).where(AdminPick.result != "pending", AdminPick.featured_public.is_(True), AdminPick.featured_public_at.is_not(None))
            ).scalars().all()
        )
        pending = db.execute(
            select(AdminPick).where(AdminPick.result == "pending", AdminPick.featured_public.is_(True), AdminPick.featured_public_at.is_not(None))
        ).scalars().all()
        for pick in pending:
            if settle_admin_pick(db, pick):
                settled.append(pick)

    if values.get("guda_picks_enabled", True):
        settled.extend(
            db.execute(
                select(FeaturedPick).where(FeaturedPick.result != "pending", FeaturedPick.featured_public.is_(True), FeaturedPick.featured_public_at.is_not(None))
            ).scalars().all()
        )
        pending_featured = db.execute(
            select(FeaturedPick).where(FeaturedPick.result == "pending", FeaturedPick.featured_public.is_(True), FeaturedPick.featured_public_at.is_not(None))
        ).scalars().all()
        for pick in pending_featured:
            if settle_featured_pick(db, pick):
                settled.append(pick)

    if settled:
        db.commit()

    if not settled:
        return AdminPickTrackRecordOut(has_data=False)

    won = sum(1 for p in settled if p.result == "won")
    lost = sum(1 for p in settled if p.result == "lost")
    unresolved = sum(1 for p in settled if p.result == "unresolved")
    decided = won + lost

    settled.sort(key=lambda p: p.settled_at or dt.datetime.min, reverse=True)
    entries: list[TrackRecordEntryOut] = []
    for pick in settled[:MAX_TRACK_RECORD_ENTRIES]:
        if isinstance(pick, AdminPick):
            legs = frozen_admin_pick_legs(db, pick)
            if not legs:
                continue
            entries.append(
                TrackRecordEntryOut(
                    kind="admin_pick", label=pick.label, legs=_legs_to_schema(legs),
                    result=pick.result, settled_at=pick.settled_at,
                )
            )
        else:
            leg = _featured_pick_to_leg(db, pick)
            if leg is None:
                continue
            entries.append(
                TrackRecordEntryOut(
                    kind="guda_pick", label=pick.note, legs=_legs_to_schema([leg]),
                    result=pick.result, settled_at=pick.settled_at,
                )
            )

    return AdminPickTrackRecordOut(
        has_data=True,
        settled_count=len(settled),
        won=won,
        lost=lost,
        unresolved=unresolved,
        hit_rate=(won / decided) if decided else 0.0,
        picks=entries,
    )


def resolve_public_teaser_pick(db: Session) -> PublicTeaserPickOut:
    """Shared by this public endpoint and routes_admin's own preview --
    always re-prices every leg live from current MatchOdds/Prediction, same
    "never a stale snapshot" rule AdminPick itself follows while pending.
    Drops to has_data=False the moment even one leg stops resolving (a
    price pulled, the match no longer SCHEDULED) rather than showing a
    partial combo -- there is no frozen/settled fallback here, unlike a
    regular AdminPick, since this is never meant to show a result."""

    pick = db.execute(select(AdminPick).where(AdminPick.public_teaser.is_(True))).scalars().first()
    if pick is None:
        return PublicTeaserPickOut(has_data=False)

    # expires_at is the earliest leg's own kickoff (see
    # promote_pick_to_public_teaser) -- once that's passed, the combo is no
    # longer "upcoming" even if its odds/prediction rows still happen to
    # resolve for a little while after.
    if pick.expires_at <= utcnow():
        return PublicTeaserPickOut(has_data=False)

    refs = [(leg["match_id"], leg["market"], leg["selection"]) for leg in pick.legs]
    legs, _warnings = price_legs(db, refs)
    return public_teaser_pick_to_schema(pick, legs)


@router.get("/teaser-pick", response_model=PublicTeaserPickOut)
def public_teaser_pick(db: Session = Depends(get_db)) -> PublicTeaserPickOut:
    """The one showcase combo an admin has chosen to show -- see
    PublicTeaserPickOut's own docstring for why this, alone among public
    endpoints, shows real selections."""

    return resolve_public_teaser_pick(db)


@router.get("/branding", response_model=BrandingOut)
def branding_endpoint(request: Request, db: Session = Depends(get_db)) -> Response:
    """The branding, served so a browser always checks it's current.

    Everything else under /api/public/ is cached for minutes (see
    app.main), which is fine for fixtures and track-record figures but not
    for what an admin edits in Settings -- the trial length, the price and
    its period, the referral bonus -- where "I changed it and the public page
    still says the old number" is the bug. ``no-cache`` makes the browser
    ask every time, and the ETag turns that ask into a tiny 304 whenever
    nothing changed, so it costs about what the old caching saved.
    """

    body = public_branding(db).model_dump_json().encode()
    etag = f'"{hashlib.sha256(body).hexdigest()[:16]}"'
    headers = {"Cache-Control": "no-cache", "ETag": etag}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type="application/json", headers=headers)


def public_branding(db: Session) -> BrandingOut:
    """Site name and default look, readable before anyone logs in.

    Public because the landing page needs it: a visitor with no account still
    has to see the right name and the right theme, and waiting for an
    authenticated call would mean a flash of the wrong one.

    Nothing here is sensitive -- it is what every visitor sees anyway.
    """

    values = app_settings.all_values(db)
    price_label, price_per_day = app_settings.price_display(values)
    return BrandingOut(
        site_name=str(values["site_name"]),
        site_tagline=str(values["site_tagline"]),
        default_theme=str(values["default_theme"]),
        default_accent=str(values["default_accent"]),
        registration_open=bool(values["registration_open"]),
        contact_whatsapp=str(values["contact_whatsapp"]),
        pricing_label=price_label,
        price_per_day=price_per_day,
        whatsapp_channel_url=str(values.get("whatsapp_channel_url") or ""),
        default_market_tab=str(values["default_market_tab"]),
        referral_enabled=bool(values["referral_enabled"]),
        referral_bonus_days=int(values["referral_bonus_days"]),
        trial_days=int(values["trial_duration_days"] or 0) if values["trial_enabled"] else 0,
    )


@router.get("/notice", response_model=NoticeOut)
def public_notice(db: Session = Depends(get_db)) -> NoticeOut:
    """A superadmin-authored banner, rendered on the Dashboard for every
    signed-in account, free tier or premium alike. Not a prediction, not
    sensitive: a maintenance window, a new league going live, or anything
    else worth a heads-up. The endpoint itself stays public/unauthenticated
    since it's cheap to serve and there's nothing here worth gating. Off by
    default, and reads as off if the message was left blank -- a notice with
    nothing to say is a bug waiting to be shown."""

    values = app_settings.all_values(db)
    message = str(values.get("notice_message") or "").strip()
    return NoticeOut(enabled=bool(values.get("notice_enabled")) and bool(message), message=message)


# A league hub page lists more of one league's fixtures than the cross-league
# teaser lists do.
MAX_LEAGUE_FIXTURES = 60
FORM_MATCHES = 5
HEAD_TO_HEAD_MATCHES = 5
# Free picks come from fixtures kicking off within FREE_PICK_WINDOW; only when
# a market has nothing left there (a quiet midweek) does it reach out to
# FREE_PICK_FALLBACK_WINDOW, so there are three picks whenever there's football.
FREE_PICK_WINDOW = dt.timedelta(hours=36)
FREE_PICK_FALLBACK_WINDOW = dt.timedelta(hours=72)
# The three free picks, in the order they're chosen: the market with the
# fewest strong candidates picks its match first.
FREE_PICK_KINDS = ("1x2", "double_chance", "over_1_5")
# A pick's match counts as over at full time -- or, if no result ever
# arrives (postponed, abandoned, a feed that stopped updating), this long
# after its published kickoff, so one dead fixture can't freeze the set.
FREE_PICK_GIVE_UP_AFTER = dt.timedelta(hours=6)


def _upcoming_fixtures(
    db: Session, *, days_ahead: int, limit: int, league: str | None = None, best_first: bool = False
) -> list[PublicFixtureOut]:
    now = utcnow()
    stmt = (
        select(Match)
        .where(Match.status == "SCHEDULED", Match.date >= now, Match.date < now + dt.timedelta(days=days_ahead))
        .options(selectinload(Match.home_team), selectinload(Match.away_team))
        .order_by(Match.date.asc())
    )
    if league:
        stmt = stmt.where(Match.league == league)
    if not best_first:
        return [_public_fixture_out(db, m) for m in db.execute(stmt.limit(limit)).scalars()]
    # Highest confidence first (then kickoff), so a showcase leads with the
    # calls the model is surest of instead of whatever kicks off next.
    candidates = [_public_fixture_out(db, m) for m in db.execute(stmt.limit(limit * 6)).scalars()]
    candidates.sort(key=lambda f: (_CONFIDENCE_RANK.get(f.confidence or "", 3), f.kickoff))
    return candidates[:limit]


@router.get("/fixtures", response_model=list[PublicFixtureOut])
def public_fixtures(
    days_ahead: int = 3,
    limit: int = 6,
    league: str | None = None,
    sort: str = "kickoff",
    db: Session = Depends(get_db),
) -> list[PublicFixtureOut]:
    """Upcoming fixtures with *whether* a prediction exists and how confident
    it is -- but never the selection or the probability. Enough to show the
    system is live and covering real matches; not enough to be the product.
    ``league`` narrows to one competition (the league hub pages);
    ``sort=confidence`` puts high-confidence fixtures first."""

    days_ahead = max(0, min(days_ahead, MAX_PUBLIC_FIXTURE_DAYS_AHEAD))
    limit = max(1, min(limit, MAX_LEAGUE_FIXTURES if league else 20))
    return _upcoming_fixtures(db, days_ahead=days_ahead, limit=limit, league=league, best_first=sort == "confidence")


def _recent_results(db: Session, team_id: int, before: dt.datetime) -> list[PublicResultOut]:
    rows = db.execute(
        select(Match)
        .where(
            Match.status == "FINISHED",
            Match.date < before,
            Match.home_score.is_not(None),
            or_(Match.home_team_id == team_id, Match.away_team_id == team_id),
        )
        .options(selectinload(Match.home_team), selectinload(Match.away_team))
        .order_by(Match.date.desc())
        .limit(FORM_MATCHES)
    ).scalars()
    out = []
    for m in rows:
        home = m.home_team_id == team_id
        gf, ga = (m.home_score, m.away_score) if home else (m.away_score, m.home_score)
        out.append(PublicResultOut(
            date=m.date, opponent=(m.away_team if home else m.home_team).name, venue="H" if home else "A",
            goals_for=gf, goals_against=ga, result="W" if gf > ga else "D" if gf == ga else "L",
        ))
    return out


def _head_to_head(db: Session, match: Match) -> list[PublicHeadToHeadOut]:
    a, b = match.home_team_id, match.away_team_id
    rows = db.execute(
        select(Match)
        .where(
            Match.status == "FINISHED",
            Match.date < match.date,
            Match.home_score.is_not(None),
            or_(and_(Match.home_team_id == a, Match.away_team_id == b), and_(Match.home_team_id == b, Match.away_team_id == a)),
        )
        .options(selectinload(Match.home_team), selectinload(Match.away_team))
        .order_by(Match.date.desc())
        .limit(HEAD_TO_HEAD_MATCHES)
    ).scalars()
    return [
        PublicHeadToHeadOut(date=m.date, league=m.league, home_team=m.home_team.name, away_team=m.away_team.name,
                            home_score=m.home_score, away_score=m.away_score)
        for m in rows
    ]


@router.get("/fixtures/{match_id}", response_model=PublicFixtureDetailOut)
def public_fixture_detail(match_id: int, db: Session = Depends(get_db)) -> PublicFixtureDetailOut:
    """One upcoming fixture's public-safe detail -- its own page (SEO: lets
    someone searching "team A vs team B prediction" land on a real page
    about that exact match instead of nowhere), same rule as the list
    above: whether a prediction exists and how confident it is, never the
    selection or probability -- unless this fixture is one of today's free picks.
    Adds each side's last five results and their recent meetings: public
    facts that make every page unique rather than one paragraph on 800 URLs.

    404s for a match that doesn't exist, has already kicked off, or is
    further out than the list endpoint ever shows (MAX_PUBLIC_FIXTURE_DAYS_AHEAD)
    -- a dead preview page for a match nobody can usefully search for yet is
    worse than no page.
    """

    match = db.get(Match, match_id)
    now = utcnow()
    if match is None or not (now <= match.date < now + dt.timedelta(days=MAX_PUBLIC_FIXTURE_DAYS_AHEAD)):
        raise HTTPException(status_code=404, detail="Fixture not found")
    pick = next((p for p in free_picks(db) if p.fixture.match_id == match.id), None)
    return PublicFixtureDetailOut(
        **_public_fixture_out(db, match).model_dump(),
        home_form=_recent_results(db, match.home_team_id, match.date),
        away_form=_recent_results(db, match.away_team_id, match.date),
        head_to_head=_head_to_head(db, match),
        free_pick=pick,
    )


def _free_pick_options(prediction: Prediction, home: str, away: str) -> dict[str, tuple[str, str, str, float]]:
    """This prediction's best call in each free-pick market, as
    {kind: (market, selection, label, probability)}."""

    result = max(
        (
            ("Home Win", f"{home} to win", prediction.home_win),
            ("Draw", "Draw", prediction.draw),
            ("Away Win", f"{away} to win", prediction.away_win),
        ),
        key=lambda o: o[2],
    )
    double = max(
        (
            ("Home/Draw", f"{home} or draw", prediction.home_win + prediction.draw),
            ("Draw/Away", f"{away} or draw", prediction.draw + prediction.away_win),
            ("Home/Away", f"{home} or {away}", prediction.home_win + prediction.away_win),
        ),
        key=lambda o: o[2],
    )
    options = {
        "1x2": ("Match Result", result[0], result[1], result[2]),
        "double_chance": ("Double Chance", double[0], double[1], double[2]),
    }
    over = (prediction.over_probabilities or {}).get("1.5")
    if over is not None:
        options["over_1_5"] = ("Total Goals 1.5", "Over 1.5", "Over 1.5 goals", over)
    return options


def _choose_free_picks(db: Session, now: dt.datetime) -> list[dict]:
    """A fresh set: the model's strongest Match Result, Double Chance and
    Over 1.5 Goals calls, each on a different match, from fixtures kicking
    off in the next 36 hours (72 if a market runs out). LOW-confidence
    predictions are never used."""

    latest = (
        select(Prediction.match_id, func.max(Prediction.created_at).label("created_at"))
        .group_by(Prediction.match_id)
        .subquery()
    )
    rows = db.execute(
        select(Prediction, Match)
        .join(latest, and_(Prediction.match_id == latest.c.match_id, Prediction.created_at == latest.c.created_at))
        .join(Match, Match.id == Prediction.match_id)
        .where(
            Match.status == "SCHEDULED",
            Match.date >= now,
            Match.date < now + FREE_PICK_FALLBACK_WINDOW,
            Prediction.confidence.in_(("HIGH", "MEDIUM")),
        )
        .options(selectinload(Match.home_team), selectinload(Match.away_team))
    ).all()
    candidates = [
        (match, _free_pick_options(prediction, match.home_team.name, match.away_team.name))
        for prediction, match in rows
    ]

    picks: list[dict] = []
    used: set[int] = set()
    for kind in FREE_PICK_KINDS:
        best = min(
            (
                (match, options[kind])
                for match, options in candidates
                if match.id not in used and kind in options
            ),
            # Soon first, then the strongest call, then the earlier kickoff.
            key=lambda c: (c[0].date >= now + FREE_PICK_WINDOW, -c[1][3], c[0].date, c[0].id),
            default=None,
        )
        if best is None:
            continue
        match, (market, selection, label, probability) = best
        used.add(match.id)
        picks.append({
            "kind": kind, "match_id": match.id, "market": market, "selection": selection,
            "label": label, "probability": probability, "kickoff": match.date.isoformat(),
        })
    return picks


def _free_pick_state(pick: dict, match: Match | None, now: dt.datetime) -> tuple[str, bool]:
    """(status, is_over) for one published pick."""

    if match is not None and match.status == "FINISHED" and match.home_score is not None and match.away_score is not None:
        won = grade_outcome(pick["market"], pick["selection"], home_score=match.home_score, away_score=match.away_score)
        return ("void" if won is None else "won" if won else "lost"), True
    if match is None or now >= dt.datetime.fromisoformat(pick["kickoff"]) + FREE_PICK_GIVE_UP_AFTER:
        return "void", True
    if match.status == "LIVE" or match.date <= now:
        return "live", False
    return "upcoming", False


def free_picks(db: Session, *, now: dt.datetime | None = None) -> list[PublicFreePickOut]:
    """Today's free picks (see PublicFreePickOut). The published set stays
    exactly as chosen until every one of its matches is over; the first
    read after that chooses -- and stores -- the next set."""

    now = now or utcnow()
    current = db.execute(
        select(PublicFreePickSet).order_by(PublicFreePickSet.created_at.desc(), PublicFreePickSet.id.desc()).limit(1)
    ).scalars().first()

    def load(picks: list[dict]) -> tuple[dict[int, Match], list[tuple[str, bool]]]:
        matches = {
            m.id: m
            for m in db.execute(
                select(Match)
                .where(Match.id.in_([p["match_id"] for p in picks]))
                .options(selectinload(Match.home_team), selectinload(Match.away_team))
            ).scalars()
        }
        return matches, [_free_pick_state(p, matches.get(p["match_id"]), now) for p in picks]

    picks = current.picks if current is not None else []
    matches, states = load(picks) if picks else ({}, [])
    if not picks or all(over for _, over in states):
        # With nothing new to offer yet, the finished set stays up, results
        # and all, rather than an empty section.
        fresh = _choose_free_picks(db, now)
        if fresh:
            db.add(PublicFreePickSet(created_at=now, picks=fresh))
            db.commit()
            picks = fresh
            matches, states = load(picks)

    out: list[PublicFreePickOut] = []
    for pick, (status, _) in zip(picks, states):
        match = matches.get(pick["match_id"])
        if match is None:
            continue
        finished = status in ("won", "lost")
        out.append(PublicFreePickOut(
            kind=pick["kind"],
            fixture=_public_fixture_out(db, match),
            market=pick["market"],
            selection=pick["selection"],
            label=pick["label"],
            probability=pick["probability"],
            status=status,
            home_score=match.home_score if finished or status == "live" else None,
            away_score=match.away_score if finished or status == "live" else None,
        ))
    return out


@router.get("/free-picks", response_model=list[PublicFreePickOut])
def public_free_picks(db: Session = Depends(get_db)) -> list[PublicFreePickOut]:
    return free_picks(db)


# How many winning slips the public pages show, newest first.
WINNING_SLIPS_SHOWN = 12


def winning_slips(db: Session, limit: int = WINNING_SLIPS_SHOWN) -> list[WinningSlipOut]:
    """The published screenshots of won slips (see models.WinningSlip),
    newest first -- the image bytes stay behind their own URL."""

    rows = db.execute(
        select(WinningSlip)
        .where(WinningSlip.published.is_(True))
        .order_by(WinningSlip.created_at.desc(), WinningSlip.id.desc())
        .limit(limit)
    ).scalars()
    return [winning_slip_to_schema(s) for s in rows]


@router.get("/winning-slips", response_model=list[WinningSlipOut])
def public_winning_slips(limit: int = WINNING_SLIPS_SHOWN, db: Session = Depends(get_db)) -> list[WinningSlipOut]:
    return winning_slips(db, max(1, min(limit, 48)))


@router.get("/winning-slips/{slip_id}/image")
def public_winning_slip_image(slip_id: int, db: Session = Depends(get_db)) -> Response:
    slip = db.get(WinningSlip, slip_id)
    if slip is None or not slip.published:
        raise HTTPException(status_code=404, detail="Image not found")
    # A slip's image never changes (it's deleted and re-posted instead), so
    # browsers and any CDN can keep it for good.
    return Response(
        content=slip.image,
        media_type=slip.content_type,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.get("/landing", response_model=LandingOut)
def public_landing(db: Session = Depends(get_db)) -> LandingOut:
    """Everything the public home page shows, in one call -- see LandingOut."""

    return LandingOut(
        branding=public_branding(db),
        stats=public_stats(db),
        track_record=public_track_record(db),
        admin_pick_record=admin_pick_track_record(db),
        fixtures=_upcoming_fixtures(db, days_ahead=2, limit=6, best_first=True),
        free_picks=free_picks(db),
        winning_slips=winning_slips(db),
        teaser_pick=public_teaser_pick(db),
    )
