from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_match_or_404
from app.api.schemas import LiveEventIn, LivePredictionOut, MatchOut, PredictionOut
from app.api.serializers import live_prediction_to_schema, match_to_schema, prediction_to_schema
from app.db.models import LivePrediction, Match, Prediction
from app.features.team_stats import compute_team_form
from app.live_engine import record_live_event
from app.prediction_service import build_prediction_for_match

router = APIRouter(prefix="/api/matches", tags=["matches"])


@router.get("", response_model=list[MatchOut])
def list_matches(
    league: str | None = None,
    date: dt.date | None = None,
    status: str | None = None,
    team_id: int | None = None,
    db: Session = Depends(get_db),
) -> list[MatchOut]:
    stmt = select(Match)
    if league:
        stmt = stmt.where(Match.league == league)
    if status:
        stmt = stmt.where(Match.status == status.upper())
    if date:
        start = dt.datetime.combine(date, dt.time.min)
        end = start + dt.timedelta(days=1)
        stmt = stmt.where(Match.date >= start, Match.date < end)
    if team_id:
        stmt = stmt.where(or_(Match.home_team_id == team_id, Match.away_team_id == team_id))
    matches = db.execute(stmt.order_by(Match.date.asc())).scalars()
    return [match_to_schema(m) for m in matches]


@router.get("/{match_id}", response_model=MatchOut)
def get_match(match: Match = Depends(get_match_or_404)) -> MatchOut:
    return match_to_schema(match)


@router.get("/{match_id}/prediction", response_model=PredictionOut)
def get_match_prediction(
    match: Match = Depends(get_match_or_404),
    force_refresh: bool = False,
    db: Session = Depends(get_db),
) -> PredictionOut:
    prediction = None
    if not force_refresh:
        prediction = db.execute(
            select(Prediction).where(Prediction.match_id == match.id).order_by(Prediction.created_at.desc())
        ).scalars().first()

    if prediction is None:
        prediction = build_prediction_for_match(db, match)

    return prediction_to_schema(prediction)


@router.get("/{match_id}/prediction-history", response_model=list[PredictionOut])
def get_match_prediction_history(match: Match = Depends(get_match_or_404), db: Session = Depends(get_db)) -> list[PredictionOut]:
    """Every stored prediction snapshot for this match, oldest first (spec
    section 37's "Prediction Timeline"). A match only has more than one entry
    if a prediction was (re)generated more than once -- e.g. by re-running
    the prediction scripts as the match approaches. This is real recorded
    history, not synthesized checkpoints."""

    rows = db.execute(
        select(Prediction).where(Prediction.match_id == match.id).order_by(Prediction.created_at.asc())
    ).scalars()
    return [prediction_to_schema(p) for p in rows]


@router.get("/{match_id}/statistics")
def get_match_statistics(match: Match = Depends(get_match_or_404), db: Session = Depends(get_db)) -> dict:
    home_form = compute_team_form(db, match.home_team_id, match.date, match.league)
    away_form = compute_team_form(db, match.away_team_id, match.date, match.league)
    return {
        "home_team": match.home_team.name,
        "away_team": match.away_team.name,
        "home_form": vars(home_form),
        "away_form": vars(away_form),
    }


@router.get("/{match_id}/lineup")
def get_match_lineup(match: Match = Depends(get_match_or_404)) -> dict:
    return {
        "status": "unavailable",
        "reason": (
            "No free confirmed-lineup data source is wired up yet. Plug a provider into "
            "app/data/providers and this endpoint starts returning real data -- the "
            "prediction recalculation-on-confirmation logic (spec section 14) is already "
            "built into the outcome engine, it just needs lineup input."
        ),
    }


@router.get("/{match_id}/news")
def get_match_news(match: Match = Depends(get_match_or_404)) -> dict:
    return {
        "status": "unavailable",
        "reason": (
            "No free team-news/NLP data source is wired up yet. See README's "
            "'player/lineup/news intelligence' extension point."
        ),
    }


@router.get("/{match_id}/live", response_model=list[LivePredictionOut])
def get_match_live(match: Match = Depends(get_match_or_404), db: Session = Depends(get_db)) -> list[LivePredictionOut]:
    rows = db.execute(
        select(LivePrediction).where(LivePrediction.match_id == match.id).order_by(LivePrediction.minute.asc())
    ).scalars()
    return [live_prediction_to_schema(r) for r in rows]


@router.post("/{match_id}/live-event", response_model=LivePredictionOut)
def post_match_live_event(
    payload: LiveEventIn,
    match: Match = Depends(get_match_or_404),
    db: Session = Depends(get_db),
) -> LivePredictionOut:
    live_prediction = record_live_event(
        db,
        match,
        minute=payload.minute,
        score_home=payload.score_home,
        score_away=payload.score_away,
        trigger_event=payload.trigger_event,
    )
    return live_prediction_to_schema(live_prediction)
