from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app import app_settings
from app.access import current_grant
from app.api.deps import get_current_user, get_db, require_active_access
from app.api.schemas import (
    AdminPickOut,
    FeaturedPickOut,
    FreePickOut,
    LeagueOutcomesOut,
    MarketOut,
    OutcomeOut,
    OutcomesOut,
    PredictionOut,
)
from app.api.serializers import admin_pick_to_schema, featured_pick_to_schema, prediction_to_schema
from app.betcode.selection import price_legs, resolve_legs_unpriced
from app.db.models import AdminPick, FeaturedPick, Match, Prediction, User
from app.outcomes.registry import NOT_A_FULL_PARTITION_GROUPS, find_outcome, outcomes_from_prediction
from app.pick_settlement import frozen_admin_pick_legs, settle_admin_pick, settle_featured_pick
from app.prediction_models.ml_model import FeatureCachePool
from app.prediction_service import build_prediction_for_match
from app.quality import is_high_confidence

router = APIRouter(prefix="/api/predictions", tags=["predictions"])


def _get_or_build_prediction(
    db: Session, match: Match, pool: FeatureCachePool | None = None
) -> Prediction:
    prediction = db.execute(
        select(Prediction).where(Prediction.match_id == match.id).order_by(Prediction.created_at.desc())
    ).scalars().first()
    if prediction is None:
        cache = pool.for_league(match.league) if pool is not None else None
        prediction = build_prediction_for_match(db, match, feature_cache=cache)
    return prediction


def _build_all(db: Session, matches: list[Match]) -> list[Prediction]:
    """Fills in whatever is missing, loading each league's history once.

    These endpoints build predictions on demand for any fixture that has
    none yet, so a cold cache means a whole day's fixtures get built inside
    one request. Without the pool each of those reloads its league's entire
    history -- the same read, repeated per match, straight out of a managed
    database's bandwidth allowance.
    """

    pool = FeatureCachePool(db)
    return [_get_or_build_prediction(db, m, pool) for m in matches]


@router.get("/today", response_model=list[PredictionOut], dependencies=[Depends(require_active_access)])
def predictions_today(league: str | None = None, db: Session = Depends(get_db)) -> list[PredictionOut]:
    today = dt.datetime.utcnow().date()
    start = dt.datetime.combine(today, dt.time.min)
    end = start + dt.timedelta(days=1)

    stmt = select(Match).where(Match.date >= start, Match.date < end)
    if league:
        stmt = stmt.where(Match.league == league)

    matches = db.execute(stmt).scalars().all()
    predictions = _build_all(db, matches)
    return [prediction_to_schema(p) for p in predictions]


@router.get("/high-confidence", response_model=list[PredictionOut], dependencies=[Depends(require_active_access)])
def predictions_high_confidence(
    league: str | None = None,
    days_ahead: int = Query(default=3, ge=0, le=14),
    db: Session = Depends(get_db),
) -> list[PredictionOut]:
    start = dt.datetime.utcnow()
    end = start + dt.timedelta(days=days_ahead)

    stmt = select(Match).where(Match.date >= start, Match.date < end)
    if league:
        stmt = stmt.where(Match.league == league)

    matches = db.execute(stmt).scalars().all()
    predictions = _build_all(db, matches)
    filtered = [
        p
        for p in predictions
        if is_high_confidence(p.global_outcome_probability, p.data_quality_score, p.model_agreement_score)
    ]
    return [prediction_to_schema(p) for p in filtered]


@router.get("/most-likely", response_model=list[PredictionOut], dependencies=[Depends(require_active_access)])
def predictions_most_likely(
    league: str | None = None,
    days_ahead: int = Query(default=1, ge=0, le=14),
    limit: int = Query(default=10, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[PredictionOut]:
    start = dt.datetime.utcnow()
    end = start + dt.timedelta(days=days_ahead)

    stmt = select(Match).where(Match.date >= start, Match.date < end)
    if league:
        stmt = stmt.where(Match.league == league)

    matches = db.execute(stmt).scalars().all()
    predictions = _build_all(db, matches)
    predictions.sort(key=lambda p: p.global_outcome_probability, reverse=True)
    return [prediction_to_schema(p) for p in predictions[:limit]]


@router.get("/outcomes", response_model=OutcomesOut, dependencies=[Depends(require_active_access)])
def browse_outcomes(
    market: list[str] | None = Query(
        None,
        description="Exact market name, e.g. 'Match Result' or 'Total Goals 2.5'. Repeat it to ask for several markets at once.",
    ),
    league: str | None = None,
    days_ahead: int = Query(7, ge=0, le=21),
    min_probability: float = Query(0.0, ge=0.0, le=1.0),
    confidence: str | None = Query(None, description="HIGH, MEDIUM or LOW"),
    limit_per_league: int = Query(60, ge=1, le=500),
    db: Session = Depends(get_db),
) -> OutcomesOut:
    """Every available betting outcome across upcoming matches, grouped by league.

    The per-match endpoints answer "what about this match". This answers the
    other question: "where is the best Over 2.5 this week", or "show me every
    correct-score call in La Liga" -- which needs outcomes compared across
    matches rather than within one.

    Two deliberate choices:

    **It reads, it does not generate.** Missing predictions are skipped rather
    than built on demand. Building one is a model evaluation; doing that for
    every fixture in a week because someone opened a page is how a browse
    screen turns into an outage.

    **Everything is loaded in three queries, whatever the result size.**
    Matches with their teams, then predictions for those matches, then the
    outcomes expand in memory. The obvious implementation -- a query per match
    -- is what this codebase has already been bitten by.

    With several ``market`` values, ``limit_per_league`` caps each market
    separately within a league, so one market's near-certain outcomes (a 99%
    Under 7.5) can't push another market's rows out of a shared cap.
    """

    now = dt.datetime.utcnow()
    cutoff = now + dt.timedelta(days=days_ahead)

    match_query = (
        select(Match)
        .where(Match.date >= now, Match.date < cutoff)
        .options(selectinload(Match.home_team), selectinload(Match.away_team))
        .order_by(Match.date.asc())
    )
    if league:
        match_query = match_query.where(Match.league == league)
    matches = list(db.execute(match_query).scalars())
    if not matches:
        return OutcomesOut(markets=[], leagues=[], total_outcomes=0, total_matches=0, days_ahead=days_ahead)

    by_id = {m.id: m for m in matches}

    # One query for every prediction, newest last so the dict keeps the latest.
    predictions = db.execute(
        select(Prediction)
        .where(Prediction.match_id.in_(list(by_id)))
        .order_by(Prediction.created_at.asc())
    ).scalars()
    latest: dict[int, Prediction] = {p.match_id: p for p in predictions}

    wanted_confidence = confidence.upper() if confidence else None
    wanted_markets = set(market) if market else None

    per_league: dict[str, list[OutcomeOut]] = {}
    matches_with_outcomes: dict[str, set[int]] = {}
    # Dicts rather than sets so markets and their selections keep the
    # registry's own order (Home Win, Draw, Away Win; Over before Under) --
    # the Markets page lays its coupon columns out in exactly this order.
    market_selections: dict[str, dict[str, None]] = {}
    market_groups: dict[str, str] = {}

    for match_id, prediction in latest.items():
        match = by_id[match_id]
        if wanted_confidence and prediction.confidence != wanted_confidence:
            continue

        for outcome in outcomes_from_prediction(prediction):
            if wanted_markets and outcome.market not in wanted_markets:
                continue
            if outcome.probability < min_probability:
                continue

            market_selections.setdefault(outcome.market, {})[outcome.selection] = None
            market_groups[outcome.market] = outcome.mutually_exclusive_group

            per_league.setdefault(match.league, []).append(
                OutcomeOut(
                    match_id=match.id,
                    league=match.league,
                    home_team=match.home_team.name,
                    away_team=match.away_team.name,
                    kickoff=match.date,
                    market=outcome.market,
                    selection=outcome.selection,
                    probability=outcome.probability,
                    confidence=prediction.confidence,
                    data_quality_score=prediction.data_quality_score,
                    model_agreement_score=prediction.model_agreement_score,
                    group=outcome.mutually_exclusive_group,
                    definition=outcome.definition,
                )
            )
            matches_with_outcomes.setdefault(match.league, set()).add(match.id)

    leagues = []
    for name in sorted(per_league):
        ranked = sorted(per_league[name], key=lambda o: (-o.probability, o.kickoff))
        if wanted_markets:
            kept: dict[str, int] = {}
            rows = []
            for o in ranked:
                if kept.get(o.market, 0) < limit_per_league:
                    kept[o.market] = kept.get(o.market, 0) + 1
                    rows.append(o)
        else:
            rows = ranked[:limit_per_league]
        leagues.append(LeagueOutcomesOut(league=name, matches=len(matches_with_outcomes[name]), outcomes=rows))

    # A group appearing on more than one market name -- "Total Goals 2.5" and
    # "Total Goals 3.5" are different markets -- is still mutually exclusive
    # within each. See NOT_A_FULL_PARTITION_GROUPS for which groups' selections
    # don't actually add up to 100% (Correct Score's truncated top-N, and
    # every Double-Chance-flavored union).
    #
    # Correct-score selections are each match's own top-N scorelines, so
    # first-seen order is just whichever match came first -- sort those.
    markets = [
        MarketOut(
            market=name,
            group=market_groups[name],
            selections=(
                sorted(selections)
                if market_groups[name] in ("correct_score", "ht_correct_score")
                else list(selections)
            ),
            outcomes=sum(1 for rows in per_league.values() for o in rows if o.market == name),
            mutually_exclusive=market_groups[name] not in NOT_A_FULL_PARTITION_GROUPS,
        )
        for name, selections in market_selections.items()
    ]

    return OutcomesOut(
        markets=markets,
        leagues=leagues,
        total_outcomes=sum(len(lg.outcomes) for lg in leagues),
        total_matches=sum(lg.matches for lg in leagues),
        days_ahead=days_ahead,
    )


@router.get("/free-picks", response_model=list[FreePickOut])
def free_picks(db: Session = Depends(get_db)) -> list[FreePickOut]:
    """One headline pick per league, for any logged-in account -- including
    one with no redeemed access code. A genuine taste of the model, not the
    product itself: no full market breakdown, no explanation, no correct
    score, and (unlike ``/today`` etc.) it never builds a prediction on
    demand, since this runs for accounts that haven't paid for that compute.

    Deliberately not gated by ``require_active_access`` -- the login-only
    dependency at the router mount is all this route gets, by design.
    """

    now = dt.datetime.utcnow()
    cutoff = now + dt.timedelta(days=3)

    matches = db.execute(
        select(Match)
        .where(Match.date >= now, Match.date < cutoff)
        .options(selectinload(Match.home_team), selectinload(Match.away_team))
    ).scalars().all()
    by_id = {m.id: m for m in matches}
    if not by_id:
        return []

    predictions = db.execute(
        select(Prediction).where(Prediction.match_id.in_(list(by_id))).order_by(Prediction.created_at.asc())
    ).scalars()
    latest: dict[int, Prediction] = {p.match_id: p for p in predictions}

    best_per_league: dict[str, tuple[Match, Prediction]] = {}
    for match_id, prediction in latest.items():
        match = by_id[match_id]
        current = best_per_league.get(match.league)
        if current is None or prediction.global_outcome_probability > current[1].global_outcome_probability:
            best_per_league[match.league] = (match, prediction)

    picks = [
        FreePickOut(
            match_id=match.id,
            league=match.league,
            home_team=match.home_team.name,
            away_team=match.away_team.name,
            kickoff=match.date,
            home_win=prediction.home_win,
            draw=prediction.draw,
            away_win=prediction.away_win,
            selection=prediction.global_outcome_selection,
            probability=prediction.global_outcome_probability,
            confidence=prediction.confidence,
            data_quality_score=prediction.data_quality_score,
            model_agreement_score=prediction.model_agreement_score,
        )
        for match, prediction in best_per_league.values()
    ]
    picks.sort(key=lambda p: -p.probability)
    return picks[:8]


@router.get("/guda-picks", response_model=list[FeaturedPickOut])
def guda_picks(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[FeaturedPickOut]:
    """Outcomes a Super Admin has chosen to highlight, for the Dashboard's
    Guda Picks section. Whether a free-tier account (rather than one with
    redeemed access) sees this at all is the operator's call -- see the
    guda_picks_enabled/guda_picks_free_tier_visible settings. While a pick
    is pending, the probability shown is always the match's current real
    probability, never a frozen number. Once its match finishes, it settles
    instead (app.pick_settlement) and stays visible with its frozen won/
    lost result for up to 14 days -- that result, not just the live pick,
    is the whole point of a Dashboard trust signal. The full history lives
    at GET /api/public/admin-picks-track-record, unbounded by this window.
    """

    values = app_settings.all_values(db)
    if not values.get("guda_picks_enabled", True):
        return []

    if user.role != "superadmin" and not values.get("guda_picks_free_tier_visible", True):
        grant = current_grant(db, user)
        has_access = grant is not None and grant.expires_at > dt.datetime.utcnow()
        if not has_access:
            return []

    now = dt.datetime.utcnow()
    recent_cutoff = now - dt.timedelta(days=14)
    picks = db.execute(
        select(FeaturedPick)
        .where(or_(FeaturedPick.result == "pending", FeaturedPick.expires_at > now, FeaturedPick.settled_at > recent_cutoff))
        .order_by(FeaturedPick.created_at.desc())
    ).scalars().all()

    out: list[FeaturedPickOut] = []
    for pick in picks:
        match = db.get(Match, pick.match_id)
        if match is None:
            continue

        if settle_featured_pick(db, pick):
            db.commit()

        if pick.result != "pending":
            if pick.expires_at <= now and (pick.settled_at is None or pick.settled_at <= recent_cutoff):
                continue
            out.append(featured_pick_to_schema(pick, match, pick.probability_at_pick or 0.0))
            continue

        if pick.expires_at <= now:
            continue

        if match.status == "FINISHED":
            # Status flipped before scores were recorded (settle_featured_pick
            # needs match.is_finished, which checks scores, not status) --
            # same ambiguous-window gap AdminPick's live resolvers have via
            # their own status=="FINISHED" check. Hidden until it can settle
            # for real, rather than shown live past kickoff with no result.
            continue

        prediction = db.execute(
            select(Prediction).where(Prediction.match_id == match.id).order_by(Prediction.created_at.desc())
        ).scalars().first()
        if prediction is None:
            continue
        outcome = find_outcome(prediction, pick.market, pick.selection)
        if outcome is None:
            continue
        out.append(featured_pick_to_schema(pick, match, outcome.probability))
    return out


@router.get("/admin-picks", response_model=list[AdminPickOut])
def admin_picks(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[AdminPickOut]:
    """Whole multi-leg slips a Super Admin has chosen to highlight, for the
    Dashboard's Admin Picks section -- same visibility rules as Guda Picks
    (admin_picks_enabled/admin_picks_free_tier_visible), but for a combo
    rather than one outcome. A still-pending slip has every leg's price and
    probability, and the resulting risk tier, recomputed live, and drops out
    entirely the moment even one leg stops resolving. Once every leg's match
    has finished, the slip is settled instead (app.pick_settlement) and kept
    visible with its frozen result -- won, lost, or awaiting manual
    confirmation -- for up to 14 days past kickoff, rather than vanishing the
    moment its match ends: that result, not just the live pick, is the whole
    point of a Dashboard trust signal. The full history lives at
    GET /api/track-record/admin-picks, unbounded by this 14-day window.
    """

    values = app_settings.all_values(db)
    if not values.get("admin_picks_enabled", True):
        return []

    # Computed unconditionally, not just when the free-tier-visibility
    # setting requires it -- a booking code is premium-gated regardless of
    # whether the pick itself is visible to everyone.
    grant = current_grant(db, user)
    viewer_has_premium = user.role == "superadmin" or (grant is not None and grant.expires_at > dt.datetime.utcnow())

    if user.role != "superadmin" and not values.get("admin_picks_free_tier_visible", True):
        if not viewer_has_premium:
            return []

    now = dt.datetime.utcnow()
    recent_cutoff = now - dt.timedelta(days=14)
    # Candidates include every still-pending pick regardless of expiry, not
    # just unexpired/recently-settled ones -- otherwise a pick whose matches
    # only just finished, past its own expires_at, would be excluded from
    # this query before settle_admin_pick ever got a chance to run on it,
    # and would then never settle at all (nothing else ever reads it).
    picks = db.execute(
        select(AdminPick)
        .where(or_(AdminPick.result == "pending", AdminPick.expires_at > now, AdminPick.settled_at > recent_cutoff))
        .order_by(AdminPick.created_at.desc())
    ).scalars().all()

    out: list[AdminPickOut] = []
    for pick in picks:
        if settle_admin_pick(db, pick):
            db.commit()

        if pick.result != "pending":
            # Settled too long ago for this bounded Dashboard widget -- the
            # unbounded public track record (admin_pick_track_record) is
            # where older history lives.
            if pick.expires_at <= now and (pick.settled_at is None or pick.settled_at <= recent_cutoff):
                continue
            out.append(admin_pick_to_schema(pick, frozen_admin_pick_legs(db, pick), viewer_has_premium=viewer_has_premium))
            continue

        if pick.expires_at <= now:
            # Still pending past its own expiry -- e.g. a combo with legs on
            # different kickoffs where only some have finished so far. Known
            # limitation: it's hidden until every leg finishes and it can
            # settle, rather than shown half-resolved.
            continue

        refs = [(leg["match_id"], leg["market"], leg["selection"]) for leg in pick.legs]
        legs, _warnings = price_legs(db, refs) if pick.priced else resolve_legs_unpriced(db, refs)
        if len(legs) != len(refs):
            continue
        out.append(admin_pick_to_schema(pick, legs, viewer_has_premium=viewer_has_premium))
    return out
