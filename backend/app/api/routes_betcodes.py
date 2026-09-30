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

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_active_access
from app.api.schemas import (
    BettingSiteOut,
    PickedLegOut,
    PicksBookingIn,
    PicksBookingOut,
    ResolvePicksIn,
    SiteCodeOut,
    SuggestCriteriaIn,
    SuggestedPicksOut,
)
from app.betcode import sites as betting_sites
from app.betcode.selection import ConfidencePick, Leg, resolve_legs_unpriced, select_legs_by_confidence

router = APIRouter(
    prefix="/api/betcodes", tags=["betcodes"], dependencies=[Depends(require_active_access)]
)


def _picked_legs_to_out(legs: list[Leg]) -> list[PickedLegOut]:
    return [
        PickedLegOut(
            match_id=leg.match_id, league=leg.league, home_team=leg.home_team, away_team=leg.away_team,
            kickoff=leg.kickoff, market=leg.market, selection=leg.selection,
            model_probability=leg.model_probability,
        )
        for leg in legs
    ]


def _combined_probability(legs: list[Leg]) -> float:
    result = 1.0
    for leg in legs:
        result *= leg.model_probability
    return result


@router.post("/suggest", response_model=SuggestedPicksOut)
def suggest(payload: SuggestCriteriaIn, db: Session = Depends(get_db)) -> SuggestedPicksOut:
    pick: ConfidencePick = select_legs_by_confidence(
        db,
        min_probability=payload.min_probability if payload.min_probability is not None else 0.65,
        max_legs=payload.max_legs if payload.max_legs is not None else 8,
        markets=tuple(payload.markets),
        leagues=tuple(payload.leagues),
        days_ahead=payload.days_ahead,
    )
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
def book_picks(payload: PicksBookingIn, db: Session = Depends(get_db)) -> PicksBookingOut:
    """Books the games and outcomes a member selected -- from /suggest,
    /resolve, or picked by hand on the Markets page -- exactly as picked.

    No stored bookmaker quote is needed: the code is the site's own slip,
    priced by the site when it's opened. Each pick is still checked against
    the match's current prediction (resolve_legs_unpriced), so nothing that
    isn't a real upcoming match and outcome is ever sent to a site.
    """

    if not payload.sites:
        raise HTTPException(status_code=422, detail="Choose at least one betting site.")
    refs = [(p.match_id, p.market, p.selection) for p in payload.picks]
    legs, warnings = resolve_legs_unpriced(db, refs)
    if not legs:
        raise HTTPException(
            status_code=422, detail=" ".join(warnings) or "No picks to book -- select some outcomes first."
        )

    return PicksBookingOut(
        legs=_picked_legs_to_out(legs),
        site_codes=[SiteCodeOut(**r.as_json()) for r in betting_sites.codes_for_sites(payload.sites, legs)],
        warnings=warnings,
    )


@router.get("/sites", response_model=list[BettingSiteOut])
def list_sites() -> list[BettingSiteOut]:
    """Every betting site a code can be asked for, and whether its
    connection is built. The booking step only offers connected ones."""

    return [BettingSiteOut(key=s.key, name=s.name, connected=s.connected) for s in betting_sites.SITES]
