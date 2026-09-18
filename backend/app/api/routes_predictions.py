from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.schemas import PredictionOut
from app.api.serializers import prediction_to_schema
from app.db.models import Match, Prediction
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


@router.get("/today", response_model=list[PredictionOut])
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


@router.get("/high-confidence", response_model=list[PredictionOut])
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


@router.get("/most-likely", response_model=list[PredictionOut])
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
