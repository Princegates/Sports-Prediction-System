"""Turns a set of selections into a real booking code -- no price of our own
involved anywhere in this file.

``/suggest`` runs the confidence-based selection engine (safest matches
first, up to a leg cap) and ``/resolve`` checks an explicit list of
(match, market, selection) picks against each match's current prediction --
both are free, stateless previews of what ``/picks`` would book, so a
member can narrow criteria or edit a pick list before committing to
anything. ``/picks`` is the one call that actually asks a betting site for a
code: the site prices its own slip when it books it, so nothing here needs
a stored bookmaker quote, a target price, or a booking-code aggregator.
"""

from __future__ import annotations

import datetime as dt
import secrets

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app import ai_generation_quota
from app.api.deps import get_db, require_active_access
from app.api.rate_limit import enforce_key
from app.api.schemas import (
    AIGenerationQuotaOut,
    BettingSiteOut,
    MessageOut,
    MyBookingSlipOut,
    PickedLegOut,
    PicksBookingIn,
    PicksBookingOut,
    RenameSavedSelectionIn,
    ResolvePicksIn,
    SavedGameOut,
    SavedSelectionIn,
    SavedSelectionOut,
    SharedSelectionOut,
    SiteCodeOut,
    SuggestCriteriaIn,
    SuggestedPicksOut,
    SuggestRandomCriteriaIn,
)
from app.betcode import sites as betting_sites
from app.betcode.selection import (
    AI_PICKS_LEAGUES,
    DEFAULT_MARKETS,
    ConfidencePick,
    Leg,
    resolve_legs_unpriced,
    select_legs_by_confidence,
    select_legs_featuring_every_market,
    select_random_legs,
)
from app.db.models import AuditLog, BookingSlip, Match, SavedSelection, SharedSelection, User
from app.timeutil import utcnow
from app.pick_settlement import frozen_booking_slip_legs, settle_booking_slip

router = APIRouter(
    prefix="/api/betcodes", tags=["betcodes"], dependencies=[Depends(require_active_access)]
)

# A member's own random draw is bounded tighter than the admin-only
# scripts/generate_random_picks.py range (10-15) -- 10-20 legs, matching
# what the product asks for here specifically.
MIN_RANDOM_LEGS = 10
MAX_RANDOM_LEGS = 20


def _picked_legs_to_out(legs: list[Leg]) -> list[PickedLegOut]:
    return [
        PickedLegOut(
            match_id=leg.match_id, league=leg.league, home_team=leg.home_team, away_team=leg.away_team,
            kickoff=leg.kickoff, market=leg.market, selection=leg.selection,
            model_probability=leg.model_probability, result=leg.leg_result,
        )
        for leg in legs
    ]


def _combined_probability(legs: list[Leg]) -> float:
    result = 1.0
    for leg in legs:
        result *= leg.model_probability
    return result


def _ai_picks_leagues(requested: tuple[str, ...]) -> tuple[str, ...]:
    """An explicit request is honored as given -- the frontend's league
    picker (components/LeaguePicker.tsx) now offers every league this
    deployment tracks, not just the 8-league AI_PICKS_LEAGUES default, same
    reasoning as _ai_picks_markets below. Nothing requested still falls
    back to that curated 8 rather than an accidental all-leagues search no
    one asked for."""

    return requested or AI_PICKS_LEAGUES


def _ai_picks_markets(requested: tuple[str, ...]) -> tuple[str, ...]:
    """An explicit request is honored as given -- the frontend's market
    picker (components/MarketPicker.tsx) now offers the full catalog the
    outcome registry prices, grouped and searchable, not the old fixed
    six-market chip row, and a member choosing a market deliberately from
    that list is a different thing from an unfiltered, un-chosen search
    silently grabbing every market (the actual problem a now-removed
    allowlist here used to guard against: an empty request landing on
    barely-above-stake extremes). That case is still covered -- nothing
    requested still falls back to DEFAULT_MARKETS, same as
    build_confidence_candidates/build_candidate_legs already do for any
    other empty-markets caller."""

    return requested or DEFAULT_MARKETS


def _ensure_ai_generation_quota(db: Session, user: User) -> None:
    """Shared by /suggest and /suggest-random -- the Refresh button on AI
    Generation hits /suggest again with exclude_match_ids set, so it must
    count the same way a fresh search does."""

    if not ai_generation_quota.has_quota_left(db, user):
        limit = ai_generation_quota.daily_limit(db, user)
        raise HTTPException(
            status_code=429,
            detail=(
                f"You've used your {limit} AI Generation run{'s' if limit != 1 else ''} for today -- "
                "it resets on a rolling 24-hour basis. Buy extra runs on the Access page, or contact a Super Admin."
            ),
        )


@router.get("/quota", response_model=AIGenerationQuotaOut)
def ai_generation_quota_status(
    user: User = Depends(require_active_access), db: Session = Depends(get_db)
) -> AIGenerationQuotaOut:
    """What the member has left today, for the AI Generation page to show --
    and to know when to offer the extras on sale."""

    return AIGenerationQuotaOut(
        daily_limit=ai_generation_quota.daily_limit(db, user),
        remaining_today=ai_generation_quota.quota_remaining(db, user),
        credits=user.ai_generation_credits or 0,
        unlimited_until=user.ai_unlimited_until if ai_generation_quota.is_unlimited_now(user) else None,
        next_free_at=ai_generation_quota.next_free_at(db, user),
    )


@router.post("/suggest", response_model=SuggestedPicksOut)
def suggest(
    payload: SuggestCriteriaIn, db: Session = Depends(get_db), user: User = Depends(require_active_access)
) -> SuggestedPicksOut:
    _ensure_ai_generation_quota(db, user)
    engine = select_legs_featuring_every_market if payload.feature_all_markets else select_legs_by_confidence
    pick: ConfidencePick = engine(
        db,
        min_probability=payload.min_probability if payload.min_probability is not None else 0.65,
        max_legs=payload.max_legs if payload.max_legs is not None else 8,
        markets=_ai_picks_markets(tuple(payload.markets)),
        leagues=_ai_picks_leagues(tuple(payload.leagues)),
        days_ahead=payload.days_ahead,
        date_from=payload.date_from,
        date_to=payload.date_to,
        exclude_match_ids=frozenset(payload.exclude_match_ids),
        exclude_legs=frozenset((leg.match_id, leg.market) for leg in payload.exclude_legs),
        user_salt=user.id,
    )
    ai_generation_quota.record_generation(db, user)
    return SuggestedPicksOut(
        legs=_picked_legs_to_out(pick.legs),
        combined_probability=pick.combined_probability,
        candidates_considered=pick.candidates_considered,
        warnings=pick.warnings,
    )


@router.post("/suggest-random", response_model=SuggestedPicksOut)
def suggest_random(
    payload: SuggestRandomCriteriaIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_active_access),
) -> SuggestedPicksOut:
    """A member's own random draw -- the same real-match candidate pool
    /suggest searches (the accuracy floor, one leg per match), picked in
    random order instead of safest-first, landing on a slip somewhere
    between min_legs and max_legs rather than one fixed count. Same preview
    contract as /suggest: book the result with POST /picks."""

    _ensure_ai_generation_quota(db, user)
    min_legs = max(MIN_RANDOM_LEGS, min(payload.min_legs, MAX_RANDOM_LEGS))
    max_legs = max(min_legs, min(payload.max_legs, MAX_RANDOM_LEGS))
    pick: ConfidencePick = select_random_legs(
        db,
        min_probability=payload.min_probability if payload.min_probability is not None else 0.60,
        min_legs=min_legs,
        max_legs=max_legs,
        markets=_ai_picks_markets(tuple(payload.markets)),
        leagues=_ai_picks_leagues(tuple(payload.leagues)),
        days_ahead=payload.days_ahead,
        date_from=payload.date_from,
        date_to=payload.date_to,
        exclude_match_ids=frozenset(payload.exclude_match_ids),
        exclude_legs=frozenset((leg.match_id, leg.market) for leg in payload.exclude_legs),
    )
    ai_generation_quota.record_generation(db, user)
    return SuggestedPicksOut(
        legs=_picked_legs_to_out(pick.legs),
        combined_probability=pick.combined_probability,
        candidates_considered=pick.candidates_considered,
        warnings=pick.warnings,
    )


@router.post("/resolve", response_model=SuggestedPicksOut)
def resolve(payload: ResolvePicksIn, db: Session = Depends(get_db)) -> SuggestedPicksOut:
    """Checks an explicit pick list (a chat answer's picks, a Markets-page
    shortlist) against each match's current prediction -- same grounding
    /suggest's own candidates get, just for picks chosen elsewhere rather
    than searched for here."""

    refs = [(p.match_id, p.market, p.selection) for p in payload.picks]
    legs, warnings = resolve_legs_unpriced(db, refs)
    return SuggestedPicksOut(
        legs=_picked_legs_to_out(legs),
        combined_probability=_combined_probability(legs),
        candidates_considered=len(payload.picks),
        warnings=warnings,
    )


@router.post("/picks", response_model=PicksBookingOut)
def book_picks(
    payload: PicksBookingIn,
    user: User = Depends(require_active_access),
    db: Session = Depends(get_db),
) -> PicksBookingOut:
    """Books the games and outcomes a member selected -- from /suggest,
    /resolve, or picked by hand on the Markets page -- exactly as picked.

    No stored bookmaker quote is needed: the code is the site's own slip,
    priced by the site when it's opened. Each pick is still checked against
    the match's current prediction (resolve_legs_unpriced), so nothing that
    isn't a real upcoming match and outcome is ever sent to a site.

    Every booking is recorded against the member's account as a
    BookingSlip, legs frozen exactly as resolved here -- see GET /mine for
    their own results history once matches finish (app.pick_settlement).
    """

    if not payload.sites:
        raise HTTPException(status_code=422, detail="Choose at least one betting site.")
    refs = [(p.match_id, p.market, p.selection) for p in payload.picks]
    legs, warnings = resolve_legs_unpriced(db, refs)
    if not legs:
        raise HTTPException(
            status_code=422, detail=" ".join(warnings) or "No picks to book -- select some outcomes first."
        )

    site_codes = betting_sites.codes_for_sites(payload.sites, legs)
    ready = next((c for c in site_codes if c.status == "code_ready"), None)
    if ready is not None:
        status = "code_ready"
    elif all(c.status == "not_connected" for c in site_codes):
        status = "provider_unavailable"
    else:
        status = "provider_error"
    first_error = next((c for c in site_codes if c.message), None)

    slip = BookingSlip(
        user_id=user.id,
        bookmaker=", ".join(c.name for c in site_codes) or ", ".join(payload.sites),
        criteria={"picks": [{"match_id": p.match_id, "market": p.market, "selection": p.selection} for p in payload.picks], "sites": payload.sites},
        legs=[leg.as_json() for leg in legs],
        combined_odds=None,
        combined_probability=_combined_probability(legs),
        expires_at=max(leg.kickoff for leg in legs) + dt.timedelta(days=2),
        provider="direct",
        status=status,
        booking_code=ready.code if ready else None,
        deep_link=ready.link if ready else None,
        provider_message=ready.message if ready else (first_error.message if first_error else None),
        site_codes=[c.as_json() for c in site_codes],
    )
    db.add(slip)
    db.add(
        AuditLog(
            actor_user_id=user.id,
            actor_email=user.email,
            action="booking_code.generated",
            target_user_id=user.id,
            detail={"sites": payload.sites, "status": status, "legs": len(legs)},
        )
    )
    db.commit()

    return PicksBookingOut(
        legs=_picked_legs_to_out(legs),
        site_codes=[SiteCodeOut(**c.as_json()) for c in site_codes],
        warnings=warnings,
    )


@router.get("/mine", response_model=list[MyBookingSlipOut])
def my_booking_slips(
    user: User = Depends(require_active_access),
    db: Session = Depends(get_db),
    limit: int = 50,
) -> list[MyBookingSlipOut]:
    """A member's own generated-code history, most recent first -- every
    code they've ever booked, settled against real results once its matches
    finish (app.pick_settlement). Nothing here is cherry-picked: a loss
    stays visible exactly like a win, which is the point of a personal
    track record."""

    slips = db.execute(
        select(BookingSlip).where(BookingSlip.user_id == user.id).order_by(BookingSlip.created_at.desc()).limit(limit)
    ).scalars().all()

    changed = False
    for slip in slips:
        if settle_booking_slip(db, slip):
            changed = True
    if changed:
        db.commit()

    out: list[MyBookingSlipOut] = []
    for slip in slips:
        legs = frozen_booking_slip_legs(slip) if slip.result != "pending" else [
            Leg(**{**leg, "kickoff": dt.datetime.fromisoformat(leg["kickoff"])}) for leg in slip.legs
        ]
        out.append(
            MyBookingSlipOut(
                id=slip.id,
                legs=_picked_legs_to_out(legs),
                combined_probability=slip.combined_probability,
                site_codes=[SiteCodeOut(**c) for c in (slip.site_codes or [])],
                result=slip.result,
                created_at=slip.created_at,
                settled_at=slip.settled_at,
            )
        )
    return out


@router.delete("/mine/{slip_id}", status_code=204)
def delete_my_booking_slip(
    slip_id: int, user: User = Depends(require_active_access), db: Session = Depends(get_db)
) -> None:
    """Removes one of the member's own codes from their history. Scoped to
    the caller -- another member's slip is a 404, not a 403, so ids can't be
    probed. A code an admin already committed to the public track record
    isn't affected: that is its own copy (AdminPick), made at the time."""

    slip = db.get(BookingSlip, slip_id)
    if slip is None or slip.user_id != user.id:
        raise HTTPException(status_code=404, detail="Code not found.")
    db.delete(slip)
    db.commit()


@router.delete("/mine", response_model=MessageOut)
def clear_my_booking_history(
    user: User = Depends(require_active_access), db: Session = Depends(get_db)
) -> MessageOut:
    """Clears the member's settled codes -- the results history. A code still
    waiting on its matches is left alone, so an upcoming slip (one an admin
    may still commit to the track record) can't vanish with a stray click;
    delete those one at a time."""

    result = db.execute(
        delete(BookingSlip).where(BookingSlip.user_id == user.id, BookingSlip.result != "pending")
    )
    db.commit()
    count = result.rowcount or 0
    return MessageOut(message=f"Cleared {count} code{'s' if count != 1 else ''} from your history.")


@router.get("/sites", response_model=list[BettingSiteOut])
def list_sites() -> list[BettingSiteOut]:
    """Every betting site a code can be asked for, and whether its
    connection is built. The booking step only offers connected ones."""

    return [BettingSiteOut(key=s.key, name=s.name, connected=s.connected) for s in betting_sites.SITES]


# --- Saved selections ------------------------------------------------------------
#
# A member can keep a set of picks to turn into a booking code later. Only the
# picks are stored; "using" one runs it back through POST /resolve, so it always
# shows today's numbers and drops any game that has started.

MAX_SAVED_SELECTIONS = 30


def _games_for(db: Session, picks: list[dict]) -> list[SavedGameOut]:
    """Each pick with its game's details and whether it can still go on a
    booking code (not started, not finished, still exists)."""

    ids = {p["match_id"] for p in picks}
    matches = {m.id: m for m in db.execute(select(Match).where(Match.id.in_(ids))).scalars()} if ids else {}
    now = utcnow()
    games: list[SavedGameOut] = []
    for pick in picks:
        match = matches.get(pick["match_id"])
        if match is None:
            games.append(
                SavedGameOut(
                    match_id=pick["match_id"], league="", home_team="Game no longer available", away_team="",
                    kickoff=None, market=pick["market"], selection=pick["selection"], upcoming=False,
                )
            )
            continue
        games.append(
            SavedGameOut(
                match_id=match.id,
                league=match.league,
                home_team=match.home_team.name,
                away_team=match.away_team.name,
                kickoff=match.date,
                market=pick["market"],
                selection=pick["selection"],
                upcoming=match.status != "FINISHED" and match.date > now,
            )
        )
    return games


def _saved_to_out(
    db: Session, saved: SavedSelection, warnings: list[str] | None = None
) -> SavedSelectionOut:
    games = _games_for(db, saved.picks or [])
    return SavedSelectionOut(
        id=saved.id,
        name=saved.name,
        created_at=saved.created_at,
        games=games,
        upcoming_count=sum(1 for g in games if g.upcoming),
        warnings=warnings or [],
    )


def _clean_name(raw: str | None) -> str:
    name = " ".join((raw or "").split())[:80]
    return name or f"Saved {utcnow():%d %b %H:%M}"


def _owned_saved(db: Session, user: User, saved_id: int) -> SavedSelection:
    saved = db.get(SavedSelection, saved_id)
    # Another member's selection is a 404, not a 403, so ids can't be probed.
    if saved is None or saved.user_id != user.id:
        raise HTTPException(status_code=404, detail="Saved selection not found.")
    return saved


@router.get("/saved", response_model=list[SavedSelectionOut])
def list_saved_selections(
    user: User = Depends(require_active_access), db: Session = Depends(get_db)
) -> list[SavedSelectionOut]:
    rows = db.execute(
        select(SavedSelection).where(SavedSelection.user_id == user.id).order_by(SavedSelection.created_at.desc())
    ).scalars().all()
    return [_saved_to_out(db, row) for row in rows]


@router.post("/saved", response_model=SavedSelectionOut)
def save_selection(
    payload: SavedSelectionIn, user: User = Depends(require_active_access), db: Session = Depends(get_db)
) -> SavedSelectionOut:
    """Keeps these picks for later. Each one is checked first (a real upcoming
    game, an outcome that exists, one pick per game), and only the valid ones
    are saved -- the rest come back as warnings, so nothing unusable is kept."""

    enforce_key(f"saved_selection-user:{user.id}", 30, 600)
    count = db.execute(select(func.count(SavedSelection.id)).where(SavedSelection.user_id == user.id)).scalar_one()
    if count >= MAX_SAVED_SELECTIONS:
        raise HTTPException(
            status_code=400,
            detail=f"You can keep up to {MAX_SAVED_SELECTIONS} saved selections. Delete one to save another.",
        )

    refs: list[tuple[int, str, str]] = []
    for pick in payload.picks:
        ref = (pick.match_id, pick.market, pick.selection)
        if ref not in refs:
            refs.append(ref)
    legs, warnings = resolve_legs_unpriced(db, refs)
    if not legs:
        raise HTTPException(
            status_code=422, detail=" ".join(warnings) or "None of these picks can be saved: they are not upcoming games."
        )

    saved = SavedSelection(
        user_id=user.id,
        name=_clean_name(payload.name),
        picks=[{"match_id": leg.match_id, "market": leg.market, "selection": leg.selection} for leg in legs],
    )
    db.add(saved)
    db.commit()
    db.refresh(saved)
    return _saved_to_out(db, saved, warnings)


@router.patch("/saved/{saved_id}", response_model=SavedSelectionOut)
def rename_saved_selection(
    saved_id: int,
    payload: RenameSavedSelectionIn,
    user: User = Depends(require_active_access),
    db: Session = Depends(get_db),
) -> SavedSelectionOut:
    saved = _owned_saved(db, user, saved_id)
    saved.name = _clean_name(payload.name)
    db.commit()
    return _saved_to_out(db, saved)


@router.delete("/saved/{saved_id}", status_code=204)
def delete_saved_selection(
    saved_id: int, user: User = Depends(require_active_access), db: Session = Depends(get_db)
) -> None:
    db.delete(_owned_saved(db, user, saved_id))
    db.commit()


# --- Sharing a slip ---------------------------------------------------------------
#
# A member can share the picks of a generated slip (or a saved selection) by
# link. Whoever opens it -- another member with access -- sees the games, can
# edit them (remove games, refresh, add their own) and only then makes their
# own booking code. The share is a copy of the picks taken at that moment, so the
# sender editing or deleting theirs later changes nothing for the recipient,
# and nothing the recipient does touches the sender's.

SHARE_LINK_DAYS = 14
MAX_ACTIVE_SHARES = 20


def _share_out(db: Session, share: SharedSelection, warnings: list[str] | None = None) -> SharedSelectionOut:
    owner = db.get(User, share.owner_user_id)
    first = (owner.name or "").split()[0] if owner is not None and (owner.name or "").strip() else None
    games = _games_for(db, share.picks or [])
    return SharedSelectionOut(
        token=share.token,
        name=share.name,
        shared_by=first,
        created_at=share.created_at,
        expires_at=share.expires_at,
        games=games,
        upcoming_count=sum(1 for g in games if g.upcoming),
        warnings=warnings or [],
    )


@router.post("/shared", response_model=SharedSelectionOut)
def share_selection(
    payload: SavedSelectionIn, user: User = Depends(require_active_access), db: Session = Depends(get_db)
) -> SharedSelectionOut:
    """Makes a link for these picks. Each one is checked first, exactly as when
    saving (a real upcoming game, an outcome that exists, one pick per game);
    only valid ones are shared and the rest come back as warnings."""

    enforce_key(f"share_selection-user:{user.id}", 20, 600)
    now = utcnow()
    active = db.execute(
        select(func.count(SharedSelection.id)).where(
            SharedSelection.owner_user_id == user.id, SharedSelection.expires_at > now
        )
    ).scalar_one()
    if active >= MAX_ACTIVE_SHARES:
        raise HTTPException(
            status_code=400,
            detail=f"You have {MAX_ACTIVE_SHARES} share links still active. Wait for one to expire before sharing another.",
        )

    refs: list[tuple[int, str, str]] = []
    for pick in payload.picks:
        ref = (pick.match_id, pick.market, pick.selection)
        if ref not in refs:
            refs.append(ref)
    legs, warnings = resolve_legs_unpriced(db, refs)
    if not legs:
        raise HTTPException(
            status_code=422, detail=" ".join(warnings) or "None of these picks can be shared: they are not upcoming games."
        )

    share = SharedSelection(
        token=secrets.token_urlsafe(16),
        owner_user_id=user.id,
        name=_clean_name(payload.name),
        picks=[{"match_id": leg.match_id, "market": leg.market, "selection": leg.selection} for leg in legs],
        created_at=now,
        expires_at=now + dt.timedelta(days=SHARE_LINK_DAYS),
    )
    db.add(share)
    db.add(AuditLog(actor_user_id=user.id, actor_email=user.email, action="betcode.shared", detail={"games": len(legs)}))
    db.commit()
    db.refresh(share)
    return _share_out(db, share, warnings)


@router.get("/shared/{token}", response_model=SharedSelectionOut)
def open_shared_selection(
    token: str, user: User = Depends(require_active_access), db: Session = Depends(get_db)
) -> SharedSelectionOut:
    """What was shared. An unknown, mistyped or expired link is the same plain
    404, so there is nothing to learn by guessing."""

    enforce_key(f"open_shared-user:{user.id}", 60, 600)
    share = db.execute(select(SharedSelection).where(SharedSelection.token == token[:64])).scalar_one_or_none()
    if share is None or share.expires_at <= utcnow():
        raise HTTPException(status_code=404, detail="This share link isn't valid any more. Ask for a new one.")
    return _share_out(db, share)
