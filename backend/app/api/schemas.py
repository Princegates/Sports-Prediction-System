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


class RegisterIn(BaseModel):
    email: str
    name: str
    password: str
    payment_reference: str | None = None


class LoginIn(BaseModel):
    email: str
    password: str


class UserOut(BaseModel):
    id: int
    email: str
    name: str
    role: str
    status: str
    theme: str | None = None
    accent_profile: str | None = None
    created_at: dt.datetime


class PreferencesIn(BaseModel):
    theme: str | None = None
    accent_profile: str | None = None


class MatchHistoryOut(BaseModel):
    match: MatchOut
    viewed_at: dt.datetime


class AdminUserOut(UserOut):
    payment_reference: str | None = None
    approved_by_user_id: int | None = None
    approved_at: dt.datetime | None = None


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class RegisterOut(BaseModel):
    message: str
    user: UserOut


class ApproveIn(BaseModel):
    payment_reference: str | None = None


class AuditLogOut(BaseModel):
    id: int
    actor_email: str | None = None
    action: str
    target_user_id: int | None = None
    detail: dict | None = None
    created_at: dt.datetime


class AdminOverviewOut(BaseModel):
    """Counts the admin dashboard leads with, so the pending-approval queue
    is visible without having to filter the user table by hand."""

    pending_users: int
    active_users: int
    suspended_users: int
    superadmins: int
    total_users: int
    matches_analyzed: int
    predictions_generated: int
    upcoming_fixtures: int
    chat_messages: int


# --- AI assistant chat ---------------------------------------------------


class ChatMessageIn(BaseModel):
    message: str
    # The match the user is viewing, so follow-ups resolve without re-naming
    # the teams.
    context_match_id: int | None = None


class ChatSourceOut(BaseModel):
    kind: str
    label: str
    ref: str | int | None = None


class ChatAnswerOut(BaseModel):
    id: int | None = None
    text: str
    intent: str
    sources: list[ChatSourceOut] = []
    suggestions: list[str] = []
    includes_probability: bool = False
    caveat: str | None = None


class ChatMessageOut(BaseModel):
    id: int
    role: str
    content: str
    intent: str | None = None
    context_match_id: int | None = None
    sources: list[ChatSourceOut] = []
    suggestions: list[str] = []
    created_at: dt.datetime


# --- Public (unauthenticated) landing-page data --------------------------


class PublicStatsOut(BaseModel):
    matches_analyzed: int
    teams_tracked: int
    leagues_covered: int
    predictions_generated: int
    upcoming_fixtures: int
    active_members: int
    league_names: list[str]
    markets_per_match: int


class PublicAccuracyOut(BaseModel):
    has_data: bool
    model_version: str | None = None
    computed_at: dt.datetime | None = None
    split: str | None = None
    leagues: list[str] = []
    accuracy: float | None = None
    log_loss: float | None = None
    brier_score: float | None = None
    matches_evaluated: int | None = None


class PublicFixtureOut(BaseModel):
    league: str
    kickoff: dt.datetime
    home_team: str
    away_team: str
    has_prediction: bool
    confidence: str | None = None


class AccountStatusOut(BaseModel):
    """Lets a registered-but-unapproved user see where they stand without
    being able to log in. Returns the same shape for an unknown email as for
    a real one, so this can't be used to enumerate registered accounts."""

    status: str
    message: str
    submitted_at: dt.datetime | None = None


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
