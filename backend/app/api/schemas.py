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
    # "active" / "expired" / "none" -- whether this user currently holds a
    # live access grant, and until when if so. Replaces the old
    # payment_reference/approved_by column pair now that access is a
    # redeemed-code grant rather than a one-time superadmin approval.
    access_status: str
    access_expires_at: dt.datetime | None = None


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class RegisterOut(BaseModel):
    message: str
    user: UserOut


class AuditLogOut(BaseModel):
    id: int
    actor_email: str | None = None
    action: str
    target_user_id: int | None = None
    detail: dict | None = None
    created_at: dt.datetime


class AdminOverviewOut(BaseModel):
    """Counts the admin dashboard leads with. ``users_without_access`` is the
    new attention queue -- active accounts that registered but haven't
    redeemed a code yet -- replacing the old pending-approval count now that
    every account can log in immediately."""

    active_users: int
    suspended_users: int
    superadmins: int
    total_users: int
    users_without_access: int
    active_access_grants: int
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


class LeagueAccuracyOut(BaseModel):
    league: str
    accuracy: float | None = None
    matches_evaluated: int | None = None


class PublicAccuracyOut(BaseModel):
    has_data: bool
    model_version: str | None = None
    computed_at: dt.datetime | None = None
    split: str | None = None
    leagues: list[str] = []
    # Match-weighted across every league in the split, not one league's
    # figure standing in for all of them.
    accuracy: float | None = None
    log_loss: float | None = None
    brier_score: float | None = None
    matches_evaluated: int | None = None
    per_league: list[LeagueAccuracyOut] = []


class PublicFixtureOut(BaseModel):
    league: str
    kickoff: dt.datetime
    home_team: str
    away_team: str
    has_prediction: bool
    confidence: str | None = None


class ConfidenceBandRecordOut(BaseModel):
    confidence: str
    graded: int
    hit_rate: float


class TrackRecordOut(BaseModel):
    """A verifiable record of what the system's own pre-match calls have
    actually done -- distinct from PublicAccuracyOut, which is the offline
    backtest's held-out figure. This grades real stored Prediction rows
    against real final scores once matches finish."""

    has_data: bool
    graded_predictions: int = 0
    hit_rate: float | None = None
    by_confidence: list[ConfidenceBandRecordOut] = []
    since: dt.datetime | None = None


class AccountStatusOut(BaseModel):
    """Where an account stands re: platform access -- "no_access" (active
    account, no live grant), "active", or "suspended". Returns the same
    ``no_access`` shape for an unknown email or wrong password as for a real
    account with no grant, so this can't be used to enumerate accounts."""

    status: str
    message: str
    submitted_at: dt.datetime | None = None


# --- Access codes ---------------------------------------------------------


class AccessCodeCreateIn(BaseModel):
    """A code is issued to one registered account and redeemable once.

    ``redemption_limit`` is deliberately absent: it is fixed at 1 rather than
    chosen. The model here is that someone registers, finds they have no
    access, pays, and is issued a code of their own -- so a shareable code is
    not a feature but a way to give away access by accident. The column and
    its enforcement remain in the database, so multi-use codes are a UI
    change away if that ever changes.
    """

    duration_days: int
    code_expires_in_days: int | None = None
    assigned_user_email: str
    notes: str | None = None

    # Email the code to assigned_user_email. Ignored when no address is given
    # -- there would be nowhere to send it -- and a delivery failure never
    # prevents the code being created; see AccessCodeCreatedOut.
    send_email: bool = False


class AccessCodeOut(BaseModel):
    id: int
    code: str
    status: str
    duration_days: int
    code_expires_at: dt.datetime | None = None
    redemption_limit: int
    redemption_count: int
    assigned_user_id: int | None = None
    assigned_email: str | None = None
    created_by_user_id: int
    created_at: dt.datetime
    revoked_at: dt.datetime | None = None
    revoked_reason: str | None = None
    notes: str | None = None


class AccessCodeCreatedOut(AccessCodeOut):
    """Identical shape to ``AccessCodeOut``, but ``code`` here is the real
    value rather than masked -- the one and only response where it is.

    ``emailed`` and ``email_error`` report delivery separately from creation,
    because the two succeed and fail independently. A code that was created
    but not delivered is still a perfectly good code; the admin just has to
    send it by hand, and needs to be told so rather than assuming it went.
    """

    emailed: bool = False
    email_error: str | None = None


class AccessGrantOut(BaseModel):
    id: int
    access_code_id: int
    status: str
    activated_at: dt.datetime
    expires_at: dt.datetime
    revoked_at: dt.datetime | None = None
    revoked_reason: str | None = None


class AccessRedeemIn(BaseModel):
    code: str


class AccessStatusOut(BaseModel):
    has_access: bool
    status: str  # "active" / "expired" / "none"
    expires_at: dt.datetime | None = None


class RevokeCodeIn(BaseModel):
    reason: str | None = None


class RevokeGrantIn(BaseModel):
    reason: str | None = None


class ExtendGrantIn(BaseModel):
    additional_days: int


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


# --- Superadmin settings ---------------------------------------------------


class SettingSpecOut(BaseModel):
    """Describes one setting so the panel can render it without hardcoding a
    form. The registry in app/app_settings.py is the source; this is its wire
    shape."""

    key: str
    kind: str
    group: str
    label: str
    help: str = ""
    secret: bool = False
    choices: list[str] = []
    minimum: float | None = None
    maximum: float | None = None


class SettingsOut(BaseModel):
    values: dict[str, object]
    # Secrets are never in `values`. This says whether one exists, which is
    # all the panel needs to show "set" vs "not set".
    secrets_set: dict[str, bool] = {}
    # Keys with a database override, i.e. not just the environment default.
    overridden: list[str] = []
    groups: dict[str, str] = {}
    specs: list[SettingSpecOut] = []


class SettingsUpdateIn(BaseModel):
    values: dict[str, object] | None = None
    # Keys to clear, falling back to the environment. Distinct from setting
    # them to "" -- that overrides with an empty value.
    reset: list[str] | None = None


class TestEmailIn(BaseModel):
    to: str | None = None


class TestEmailOut(BaseModel):
    sent: bool
    detail: str


class SystemStatusOut(BaseModel):
    database_reachable: bool
    matches: int
    predictions: int
    upcoming_fixtures: int
    leagues: list[str]
    users: int
    active_grants: int
    unredeemed_codes: int
    latest_match_date: dt.datetime | None = None
    latest_prediction_at: dt.datetime | None = None
    model_files: list[str] = []
    calibrator_files: int = 0
    models_built_at: dt.datetime | None = None
    email_configured: bool = False
    settings_overridden: int = 0


class BrandingOut(BaseModel):
    """Public site identity and default look, needed before anyone logs in."""

    site_name: str
    site_tagline: str
    default_theme: str
    default_accent: str
    registration_open: bool


# --- Outcome browser -------------------------------------------------------


class OutcomeOut(BaseModel):
    match_id: int
    league: str
    home_team: str
    away_team: str
    kickoff: dt.datetime
    market: str
    selection: str
    probability: float
    confidence: str
    data_quality_score: float
    # Selections sharing a group are mutually exclusive and sum to ~1. Ones
    # that don't can all happen in the same match, so stacking them is not a
    # sure thing however good each looks alone.
    group: str
    definition: str


class MarketOut(BaseModel):
    market: str
    group: str
    selections: list[str]
    outcomes: int
    mutually_exclusive: bool


class LeagueOutcomesOut(BaseModel):
    league: str
    matches: int
    outcomes: list[OutcomeOut]


class OutcomesOut(BaseModel):
    """Every available betting outcome in one window, grouped by league.

    ``markets`` is derived from what is actually present rather than a fixed
    list, so the picker can never offer a market with nothing behind it.
    """

    markets: list[MarketOut]
    leagues: list[LeagueOutcomesOut]
    total_outcomes: int
    total_matches: int
    days_ahead: int
