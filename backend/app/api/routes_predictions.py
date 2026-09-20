from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
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
from app.outcomes.registry import find_outcome, outcomes_from_prediction
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
    market: str | None = Query(None, description="Exact market name, e.g. 'Match Result' or 'Total Goals 2.5'"),
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

    per_league: dict[str, list[OutcomeOut]] = {}
    matches_with_outcomes: dict[str, set[int]] = {}
    market_selections: dict[str, set[str]] = {}
    market_groups: dict[str, str] = {}

    for match_id, prediction in latest.items():
        match = by_id[match_id]
        if wanted_confidence and prediction.confidence != wanted_confidence:
            continue

        for outcome in outcomes_from_prediction(prediction):
            if market and outcome.market != market:
                continue
            if outcome.probability < min_probability:
                continue

            market_selections.setdefault(outcome.market, set()).add(outcome.selection)
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
                    group=outcome.mutually_exclusive_group,
                    definition=outcome.definition,
                )
            )
            matches_with_outcomes.setdefault(match.league, set()).add(match.id)

    leagues = []
    for name in sorted(per_league):
        rows = sorted(per_league[name], key=lambda o: (-o.probability, o.kickoff))[:limit_per_league]
        leagues.append(LeagueOutcomesOut(league=name, matches=len(matches_with_outcomes[name]), outcomes=rows))

    # A group appearing on more than one market name -- "Total Goals 2.5" and
    # "Total Goals 3.5" are different markets -- is still mutually exclusive
    # within each. Correct Score is the one group whose members are many.
    markets = [
        MarketOut(
            market=name,
            group=market_groups[name],
            selections=sorted(market_selections[name]),
            outcomes=sum(1 for rows in per_league.values() for o in rows if o.market == name),
            mutually_exclusive=market_groups[name] != "correct_score",
        )
        for name in sorted(market_selections)
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
    guda_picks_enabled/guda_picks_free_tier_visible settings -- but the
    outcome shown is always the match's current real probability, never a
    frozen number, so it drops out on its own the moment it stops applying.
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
    picks = db.execute(
        select(FeaturedPick).where(FeaturedPick.expires_at > now).order_by(FeaturedPick.created_at.desc())
    ).scalars().all()

    out: list[FeaturedPickOut] = []
    for pick in picks:
        match = db.get(Match, pick.match_id)
        if match is None:
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
    rather than one outcome. Every leg's price and probability, and the
    resulting risk tier, are recomputed live; a slip drops out entirely the
    moment even one leg stops resolving.
    """

    values = app_settings.all_values(db)
    if not values.get("admin_picks_enabled", True):
        return []

    if user.role != "superadmin" and not values.get("admin_picks_free_tier_visible", True):
        grant = current_grant(db, user)
        has_access = grant is not None and grant.expires_at > dt.datetime.utcnow()
        if not has_access:
            return []

    now = dt.datetime.utcnow()
    picks = db.execute(
        select(AdminPick).where(AdminPick.expires_at > now).order_by(AdminPick.created_at.desc())
    ).scalars().all()

    out: list[AdminPickOut] = []
    for pick in picks:
        refs = [(leg["match_id"], leg["market"], leg["selection"]) for leg in pick.legs]
        legs, _warnings = price_legs(db, refs) if pick.priced else resolve_legs_unpriced(db, refs)
        if len(legs) != len(refs):
            continue
        out.append(admin_pick_to_schema(pick, legs))
    return out
