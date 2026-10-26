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

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import ai_generation_quota
from app.api.deps import get_db, require_active_access
from app.api.schemas import (
    AIGenerationQuotaOut,
    BettingSiteOut,
    MyBookingSlipOut,
    PickedLegOut,
    PicksBookingIn,
    PicksBookingOut,
    ResolvePicksIn,
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
from app.db.models import AuditLog, BookingSlip, User
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


@router.get("/sites", response_model=list[BettingSiteOut])
def list_sites() -> list[BettingSiteOut]:
    """Every betting site a code can be asked for, and whether its
    connection is built. The booking step only offers connected ones."""

    return [BettingSiteOut(key=s.key, name=s.name, connected=s.connected) for s in betting_sites.SITES]
