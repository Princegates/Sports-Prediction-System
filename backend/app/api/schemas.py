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
    # status alone can say "LIVE" for a simulated event pushed from this
    # match's own Live tab, or a real fixture stuck from a missed sync poll
    # -- this is the same recency check /api/matches?status=LIVE applies,
    # so any page can trust it without re-deriving the staleness window.
    is_live: bool


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
    # The next-best outcomes after the headline pick, each with its own
    # probability -- never combined with it or with each other. See
    # app.outcomes.engine.secondary_outcomes for why Double Chance never
    # appears here. Shorter than 2 for a thin-data match; the same
    # data-quality gate that thins the headline pick applies here too.
    also_likely: list[GlobalOutcomeOut] = []
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


class UpdateProfileIn(BaseModel):
    name: str


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str


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


class ChatPickOut(BaseModel):
    match_id: int
    market: str
    selection: str


class ChatAnswerOut(BaseModel):
    id: int | None = None
    text: str
    intent: str
    sources: list[ChatSourceOut] = []
    suggestions: list[str] = []
    includes_probability: bool = False
    caveat: str | None = None
    picks: list[ChatPickOut] = []
    # True when the optional LLM rewriter actually replaced the grounded
    # text -- lets the UI show that it's looking at phrased prose.
    rewritten: bool = False


class ChatMessageOut(BaseModel):
    id: int
    role: str
    content: str
    intent: str | None = None
    context_match_id: int | None = None
    sources: list[ChatSourceOut] = []
    suggestions: list[str] = []
    picks: list[ChatPickOut] = []
    rewritten: bool = False
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
    activated_at: dt.datetime | None = None
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
    contact_whatsapp: str
    # Which Markets page coupon tab (match_result/double_chance/btts/
    # draw_no_bet/other) opens by default -- see app_settings.py.
    default_market_tab: str


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


class NoticeOut(BaseModel):
    enabled: bool
    message: str


class FreePickOut(BaseModel):
    """The free tier's shape: a headline pick, not the product. Deliberately
    thinner than ``PredictionOut`` -- no full market breakdown, no
    explanation, no correct-score detail -- so the free/premium boundary
    holds at the API itself, not just in what the frontend chooses to show."""

    match_id: int
    league: str
    home_team: str
    away_team: str
    kickoff: dt.datetime
    home_win: float
    draw: float
    away_win: float
    selection: str
    probability: float
    confidence: str


class FeaturePickIn(BaseModel):
    """An admin's choice of which real outcome to promote -- a reference
    into a match's own already-computed markets, never a typed-in claim."""

    match_id: int
    market: str
    selection: str
    note: str | None = None


class FeaturePickUpdateIn(BaseModel):
    """Edits an already-featured Guda Pick's note in place -- the match,
    market and selection it references never change; to feature a
    different outcome, unfeature this one and create a new one."""

    note: str | None = None


class FeaturedPickOut(BaseModel):
    id: int
    match: MatchOut
    market: str
    selection: str
    # Recomputed from the match's latest Prediction at read time, never a
    # stored snapshot -- see FeaturedPick's docstring for why.
    probability: float
    note: str | None
    created_at: dt.datetime
    expires_at: dt.datetime


class AdminPickLegIn(BaseModel):
    match_id: int
    market: str
    selection: str


class AdminPickIn(BaseModel):
    """A whole slip an admin chose to promote -- a list of (match, market,
    selection) references, same reasoning as FeaturePickIn: never a typed-in
    probability or price, re-resolved from scratch server-side.

    ``priced`` picks a resolver: True (the default, an AI Generation slip)
    re-prices every leg from MatchOdds via app.betcode.selection.price_legs,
    rejecting the slip if even one leg has no stored bookmaker quote. False
    resolves legs by model probability alone (resolve_legs_unpriced), no
    quote required -- for a slip built from outcomes that were never priced,
    like Markets' "My picks" panel.

    ``booking_code``/``booking_code_bookmaker`` are never generated or
    verified by this platform -- see AdminPick's own docstring. Leaving both
    blank is the normal case; if either is set, both must be, since a code
    with no named bookmaker is unusable and a bookmaker with no code is
    just noise.
    """

    legs: list[AdminPickLegIn]
    label: str | None = None
    note: str | None = None
    priced: bool = True
    booking_code: str | None = None
    booking_code_bookmaker: str | None = None


class AdminPickLegOut(BaseModel):
    match_id: int
    league: str
    home_team: str
    away_team: str
    kickoff: dt.datetime
    market: str
    selection: str
    probability: float
    # Both None together on an unpriced pick's leg -- see AdminPickOut.priced.
    decimal_odds: float | None
    priced_by: str | None


class AdminPickOut(BaseModel):
    id: int
    legs: list[AdminPickLegOut]
    # Whether this slip was priced from real bookmaker quotes at all --
    # combined_odds is only ever present when this is True.
    priced: bool
    # Recomputed from every leg's current Prediction/MatchOdds at read time,
    # never a stored snapshot -- see AdminPick's docstring for why. None on
    # an unpriced slip -- there is no combined price to show.
    combined_odds: float | None
    combined_probability: float
    risk_tier: str  # "low" | "medium" | "high" -- see betcode.selection.risk_tier
    label: str | None
    note: str | None
    # "system_weekly_<tier>" for a row scripts/generate_weekly_picks.py
    # produced, null for anything an admin built by hand -- lets the
    # frontend split the two into separate sections without a second
    # endpoint. See AdminPick.source.
    source: str | None
    # Whether a booking code exists on this pick at all, regardless of
    # whether *this viewer* is allowed to see it -- lets a free-tier viewer
    # be shown "a code is available, subscribe to see it" instead of no
    # signal at all. booking_code/booking_code_bookmaker are populated only
    # for a viewer with active premium access; null for everyone else even
    # when has_booking_code is True.
    has_booking_code: bool
    booking_code: str | None
    booking_code_bookmaker: str | None
    created_at: dt.datetime
    expires_at: dt.datetime


# --- Booking codes -----------------------------------------------------


class BetCodeCriteriaIn(BaseModel):
    bookmaker: str  # who the generated code is for
    target_odds: float
    markets: list[str] = []
    min_probability: float | None = None
    max_legs: int | None = None
    # Kept for older callers that only ever meant one league; ignored
    # whenever `leagues` below is non-empty.
    league: str | None = None
    # Empty = every league this deployment has data for; non-empty = any one
    # of these (a match only has one league, so this is a union, not an
    # intersection) -- lets AI Generation search several leagues at once
    # without also having to mean "all of them".
    leagues: list[str] = []
    days_ahead: int = 7
    # Whose captured prices to build legs from. Unset = any bookmaker this
    # project has a real quote from -- see app.betcode.selection's module
    # docstring for why that differs from `bookmaker` above.
    price_bookmaker: str | None = None


class BetCodeLegOut(BaseModel):
    match_id: int
    league: str
    home_team: str
    away_team: str
    kickoff: dt.datetime
    market: str
    selection: str
    model_probability: float
    decimal_odds: float
    priced_by: str  # which bookmaker's stored quote this price came from


class BetCodePreviewOut(BaseModel):
    """The selection step's own output -- what the AI picked and why, before
    anything is sent anywhere. No provider is called to produce this, so
    it's free to preview repeatedly while narrowing down criteria."""

    legs: list[BetCodeLegOut]
    combined_odds: float
    combined_probability: float
    target_odds: float
    met_target: bool
    candidates_considered: int
    warnings: list[str]


class BetCodePickIn(BaseModel):
    """An explicit (match, market, selection) to price -- no search, no
    criteria, just "what does this exact selection cost right now". This is
    what a chat picks list or the Markets page's own shortlist sends over;
    see app.betcode.selection.price_legs."""

    match_id: int
    market: str
    selection: str


class BetCodePriceIn(BaseModel):
    picks: list[BetCodePickIn]
    # Same meaning as BetCodeCriteriaIn.price_bookmaker: unset = any
    # bookmaker this project has a real quote from.
    price_bookmaker: str | None = None


class BetCodeGenerateIn(BaseModel):
    criteria: BetCodeCriteriaIn
    # Pass the exact legs a prior /preview call returned, so what gets sent
    # to the aggregator is provably what was shown on screen -- omit to run
    # selection fresh instead.
    legs: list[BetCodeLegOut] | None = None


class BetCodeOut(BaseModel):
    id: int
    created_at: dt.datetime
    bookmaker: str
    legs: list[BetCodeLegOut]
    combined_odds: float
    combined_probability: float
    expires_at: dt.datetime
    provider: str
    status: str
    booking_code: str | None
    deep_link: str | None
    provider_message: str | None
