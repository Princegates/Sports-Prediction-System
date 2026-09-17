from __future__ import annotations

import datetime as dt

from pydantic import BaseModel


class TeamOut(BaseModel):
    id: int
    name: str
    league: str
    country: str | None = None


class MatchOut(BaseModel):
    id: int
    league: str
    season: str
    date: dt.datetime
    status: str
    home_team: TeamOut
    away_team: TeamOut
    home_score: int | None
    away_score: int | None


class GlobalOutcomeOut(BaseModel):
    market: str
    selection: str
    probability: float


class PredictionOut(BaseModel):
    match_id: int
    created_at: dt.datetime
    model_version: str
    home_win: float
    draw: float
    away_win: float
    over_probabilities: dict[str, float]
    btts_yes: float
    btts_no: float
    correct_score_probabilities: dict[str, float]
    most_likely_score: str
    most_likely_score_probability: float
    global_outcome: GlobalOutcomeOut
    confidence: str
    data_quality_score: float
    model_agreement_score: float
    explanation: dict[str, list[str]]
    model_breakdown: dict


class LiveEventIn(BaseModel):
    minute: int
    score_home: int
    score_away: int
    trigger_event: str


class LivePredictionOut(BaseModel):
    match_id: int
    created_at: dt.datetime
    minute: int
    score_home: int
    score_away: int
    home_win: float
    draw: float
    away_win: float
    over_probabilities: dict[str, float]
    btts_yes: float
    global_outcome: GlobalOutcomeOut
    trigger_event: str
