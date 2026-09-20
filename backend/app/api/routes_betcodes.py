"""Generate a multi-match booking code toward a target price.

Two calls, deliberately kept separate:

``/preview`` runs the selection engine only -- no aggregator is contacted,
nothing is stored. Free to call repeatedly while narrowing target odds,
markets or accuracy, the same way adjusting filters on the Markets page is
free.

``/`` (POST) takes the legs a preview returned -- or, if none were given,
runs selection fresh -- and is the one call that may spend an aggregator
request and always persists a row, whether or not a code came back. A slip
whose provider was not configured is still saved: the selections and
combined price are real and worth keeping in the user's history even without
a redeemable code attached.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import app_settings
from app.api.deps import get_current_user, get_db, require_active_access
from app.api.schemas import (
    BetCodeCriteriaIn,
    BetCodeGenerateIn,
    BetCodeLegOut,
    BetCodeOut,
    BetCodePreviewOut,
    BetCodePriceIn,
)
from app.betcode.providers import BookingCodeError, ProviderNotConfigured, get_provider
from app.betcode.selection import Leg, SlipCriteria, price_legs, select_legs
from app.db.models import BookingSlip, User

router = APIRouter(
    prefix="/api/betcodes", tags=["betcodes"], dependencies=[Depends(require_active_access)]
)


def _criteria_from_in(db: Session, payload: BetCodeCriteriaIn) -> SlipCriteria:
    defaults = app_settings.all_values(db)
    return SlipCriteria(
        bookmaker=payload.bookmaker,
        target_odds=payload.target_odds,
        markets=tuple(payload.markets),
        min_probability=(
            payload.min_probability
            if payload.min_probability is not None
            else float(defaults.get("betcode_min_probability") or 0.65)
        ),
        max_legs=(
            payload.max_legs if payload.max_legs is not None else int(defaults.get("betcode_max_legs") or 8)
        ),
        league=payload.league,
        days_ahead=payload.days_ahead,
        price_bookmaker=payload.price_bookmaker,
    )


def _legs_to_out(legs: list[Leg]) -> list[BetCodeLegOut]:
    return [
        BetCodeLegOut(
            match_id=leg.match_id, league=leg.league, home_team=leg.home_team, away_team=leg.away_team,
            kickoff=leg.kickoff, market=leg.market, selection=leg.selection,
            model_probability=leg.model_probability, decimal_odds=leg.decimal_odds, priced_by=leg.priced_by,
        )
        for leg in legs
    ]


def _legs_from_in(legs: list[BetCodeLegOut]) -> list[Leg]:
    return [
        Leg(
            match_id=leg.match_id, league=leg.league, home_team=leg.home_team, away_team=leg.away_team,
            kickoff=leg.kickoff, market=leg.market, selection=leg.selection,
            model_probability=leg.model_probability, decimal_odds=leg.decimal_odds, priced_by=leg.priced_by,
        )
        for leg in legs
    ]


def _slip_to_out(slip: BookingSlip) -> BetCodeOut:
    return BetCodeOut(
        id=slip.id, created_at=slip.created_at, bookmaker=slip.bookmaker,
        legs=[BetCodeLegOut(**leg) for leg in slip.legs],
        combined_odds=slip.combined_odds, combined_probability=slip.combined_probability,
        expires_at=slip.expires_at, provider=slip.provider, status=slip.status,
        booking_code=slip.booking_code, deep_link=slip.deep_link, provider_message=slip.provider_message,
    )


@router.post("/preview", response_model=BetCodePreviewOut)
def preview(payload: BetCodeCriteriaIn, db: Session = Depends(get_db)) -> BetCodePreviewOut:
    criteria = _criteria_from_in(db, payload)
    result = select_legs(db, criteria)
    return BetCodePreviewOut(
        legs=_legs_to_out(result.legs),
        combined_odds=result.combined_odds,
        combined_probability=result.combined_probability,
        target_odds=criteria.target_odds,
        met_target=result.met_target,
        candidates_considered=result.candidates_considered,
        warnings=result.warnings,
    )


@router.post("/price", response_model=BetCodePreviewOut)
def price(payload: BetCodePriceIn, db: Session = Depends(get_db)) -> BetCodePreviewOut:
    """Prices an explicit list of picks -- no search, no target odds, just
    "what do these cost right now". This is what a chat answer's "send to
    AI Generation" button or a Markets-page shortlist calls: the picks were
    already chosen elsewhere, this only attaches real numbers to them.

    Reuses BetCodePreviewOut so the same result UI (legs table, combined
    odds/probability, warnings) renders it -- target_odds is set to
    whatever combined_odds came out to and met_target is always True, since
    there was never a target to fall short of here.
    """

    refs = [(p.match_id, p.market, p.selection) for p in payload.picks]
    legs, warnings = price_legs(db, refs, price_bookmaker=payload.price_bookmaker)
    combined_odds = 1.0
    combined_probability = 1.0
    for leg in legs:
        combined_odds *= leg.decimal_odds
        combined_probability *= leg.model_probability

    return BetCodePreviewOut(
        legs=_legs_to_out(legs),
        combined_odds=combined_odds,
        combined_probability=combined_probability,
        target_odds=combined_odds,
        met_target=True,
        candidates_considered=len(payload.picks),
        warnings=warnings,
    )


@router.post("", response_model=BetCodeOut)
def generate(
    payload: BetCodeGenerateIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> BetCodeOut:
    criteria = _criteria_from_in(db, payload.criteria)

    if payload.legs is not None:
        legs = _legs_from_in(payload.legs)
        combined_odds = 1.0
        combined_probability = 1.0
        for leg in legs:
            combined_odds *= leg.decimal_odds
            combined_probability *= leg.model_probability
    else:
        result = select_legs(db, criteria)
        legs = result.legs
        combined_odds = result.combined_odds
        combined_probability = result.combined_probability

    if not legs:
        raise HTTPException(status_code=422, detail="No legs qualify for these criteria -- nothing to send.")

    expires_at = min(leg.kickoff for leg in legs)
    values = app_settings.all_values(db)
    provider_name = str(values.get("betcode_provider") or "none")

    slip = BookingSlip(
        user_id=user.id,
        bookmaker=criteria.bookmaker,
        criteria=criteria.as_json(),
        legs=[leg.as_json() for leg in legs],
        combined_odds=combined_odds,
        combined_probability=combined_probability,
        expires_at=expires_at,
        provider=provider_name,
        status="selected",
    )

    try:
        provider = get_provider(db)
        outcome = provider.create_slip(bookmaker=criteria.bookmaker, legs=legs)
    except ProviderNotConfigured as exc:
        slip.status = "provider_unavailable"
        slip.provider_message = str(exc)
    except BookingCodeError as exc:
        slip.status = "provider_error"
        slip.provider_message = str(exc)
    else:
        slip.status = "code_ready"
        slip.booking_code = outcome.code
        slip.deep_link = outcome.deep_link

    db.add(slip)
    db.commit()
    db.refresh(slip)
    return _slip_to_out(slip)


@router.get("", response_model=list[BetCodeOut])
def history(
    limit: int = Query(default=20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[BetCodeOut]:
    slips = db.execute(
        select(BookingSlip)
        .where(BookingSlip.user_id == user.id)
        .order_by(BookingSlip.created_at.desc())
        .limit(limit)
    ).scalars()
    return [_slip_to_out(s) for s in slips]


@router.get("/{slip_id}", response_model=BetCodeOut)
def get_slip(slip_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> BetCodeOut:
    slip = db.get(BookingSlip, slip_id)
    if slip is None or (slip.user_id != user.id and user.role != "superadmin"):
        raise HTTPException(status_code=404, detail=f"Booking slip {slip_id} not found")
    return _slip_to_out(slip)
