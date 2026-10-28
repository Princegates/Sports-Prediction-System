from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field


class TeamOut(BaseModel):
    id: int
    name: str
    league: str
    country: str | None = None
    # Club crest, or None until the club's API-Football id is known (see
    # Team.api_football_team_id) -- the UI shows its initials meanwhile.
    logo_url: str | None = None


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
    # Embedded rather than left for the caller to fetch separately: a page
    # listing predictions (AI Predictions, Dashboard) used to fire one
    # /api/matches/{id} request per row just for team names and kickoff
    # time -- fine for a handful of matches, but "All leagues" easily means
    # hundreds of predictions, so hundreds of extra requests racing each
    # other for a database connection at once. This comes along for free in
    # the same query that already builds the prediction.
    match: MatchOut
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


# Upper bounds on free-text input. Nothing legitimate comes close; without
# them a request could store megabytes in a name or make PBKDF2 chew on a
# huge password.
EMAIL_MAX = 255
NAME_MAX = 120
PASSWORD_MAX = 256


class RegisterIn(BaseModel):
    email: str = Field(max_length=EMAIL_MAX)
    name: str = Field(max_length=NAME_MAX)
    password: str = Field(max_length=PASSWORD_MAX)
    date_of_birth: dt.date
    # Another account's referral_code -- optional, and invalid rather than
    # silently ignored if it doesn't resolve, so a typo doesn't cost the
    # referee their bonus without either side knowing.
    referral_code: str | None = Field(default=None, max_length=32)


class LoginIn(BaseModel):
    email: str = Field(max_length=EMAIL_MAX)
    password: str = Field(max_length=PASSWORD_MAX)


class UserOut(BaseModel):
    id: int
    email: str
    name: str
    role: str
    status: str
    theme: str | None = None
    accent_profile: str | None = None
    notify_weekly_picks: bool = True
    created_at: dt.datetime
    # This account's own code for referring others -- see
    # app.access.ensure_referral_code. Optional only in the schema sense
    # (every real account has one by the time anything reads it back).
    referral_code: str | None = None
    # False until the emailed link is used -- the app shows a "confirm your
    # email to start your trial" banner while it is.
    email_verified: bool = False
    # Set while a self-set break (POST /api/auth/pause) is running.
    paused_until: dt.datetime | None = None


class PreferencesIn(BaseModel):
    theme: str | None = None
    accent_profile: str | None = None
    notify_weekly_picks: bool | None = None


class UpdateProfileIn(BaseModel):
    name: str = Field(max_length=NAME_MAX)


class ChangePasswordIn(BaseModel):
    current_password: str = Field(max_length=PASSWORD_MAX)
    new_password: str = Field(max_length=PASSWORD_MAX)


class TokenIn(BaseModel):
    token: str = Field(max_length=2048)


class VerifyEmailOut(BaseModel):
    message: str
    already_verified: bool
    access_days: int = 0


class VerificationStatusOut(BaseModel):
    pending: bool
    trial_days: int = 0


class ForgotPasswordIn(BaseModel):
    email: str = Field(max_length=EMAIL_MAX)


class ResetPasswordIn(BaseModel):
    token: str = Field(max_length=2048)
    new_password: str = Field(max_length=PASSWORD_MAX)


class MessageOut(BaseModel):
    message: str


class PauseIn(BaseModel):
    days: int


class ReferralStatsOut(BaseModel):
    joined: int
    paid: int
    bonus_days_each: int
    enabled: bool


class MatchHistoryOut(BaseModel):
    match: MatchOut
    viewed_at: dt.datetime


class AdminUserOut(UserOut):
    # "active" / "expired" / "none" -- whether this user currently holds a
    # live access grant, and until when if so; "unlimited" for a superadmin,
    # who never needs one. Replaces the old
    # payment_reference/approved_by column pair now that access is a
    # redeemed-code grant rather than a one-time superadmin approval.
    access_status: str
    access_expires_at: dt.datetime | None = None
    # None means this account uses the global ai_generation_daily_limit
    # setting -- see User.daily_ai_generation_limit_override.
    ai_generation_daily_limit_override: int | None = None


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class RegisterOut(BaseModel):
    message: str
    user: UserOut


class AuditLogOut(BaseModel):
    id: int
    actor_user_id: int | None = None
    actor_email: str | None = None
    action: str
    target_user_id: int | None = None
    detail: dict | None = None
    created_at: dt.datetime


class AuditLogPageOut(BaseModel):
    """``total`` is the filtered count, not the whole table -- it's what a
    pager needs to know whether there's a next page."""

    items: list[AuditLogOut]
    total: int


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
    # Length is checked by the assistant engine (MAX_MESSAGE_LENGTH), which
    # answers with a readable 400 rather than a schema 422.
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
    # True when the optional LLM answered a message the grounded pipeline
    # couldn't match to anything, rather than the canned capability menu --
    # distinct from rewritten: this text isn't grounded in the system's own
    # data at all, so the UI must label it differently.
    general_chat: bool = False


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
    general_chat: bool = False
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
    # Included so a listing can link to this fixture's own preview page
    # (GET /api/public/fixtures/{match_id}) -- still never the selection or
    # probability, just the id needed to build a URL.
    match_id: int
    league: str
    kickoff: dt.datetime
    home_team: str
    away_team: str
    has_prediction: bool
    confidence: str | None = None
    # Crests, so public fixture lists and share cards can show them --
    # None until the club's provider id is known (see TeamOut.logo_url).
    home_logo_url: str | None = None
    away_logo_url: str | None = None


class PublicResultOut(BaseModel):
    """One past match from one side's point of view -- public facts only
    (date, opponent, score), never a prediction."""

    date: dt.datetime
    opponent: str
    venue: str  # "H" or "A"
    goals_for: int
    goals_against: int
    result: str  # "W" / "D" / "L"


class PublicHeadToHeadOut(BaseModel):
    date: dt.datetime
    league: str
    home_team: str
    away_team: str
    home_score: int
    away_score: int


class PublicFixtureDetailOut(PublicFixtureOut):
    """The fixture page's own payload: the list shape plus the public facts
    that make each page genuinely about its match (recent results, past
    meetings) rather than the same paragraph on 800 URLs."""

    home_form: list[PublicResultOut] = []
    away_form: list[PublicResultOut] = []
    head_to_head: list[PublicHeadToHeadOut] = []
    # Set only when this fixture is one of today's free picks (see PublicFreePickOut).
    free_pick: "PublicFreePickOut | None" = None


class PublicFreePickOut(BaseModel):
    """One of today's three free picks, shown in full to everyone, logged in
    or not: one Match Result (1X2), one Double Chance and one Over 1.5 Goals,
    each on a different match kicking off soon, kept until all three are
    over (see PublicFreePickSet). A deliberate marketing
    exception to "selections stay behind the login", like the public teaser
    -- three a day samples the product rather than replacing it."""

    kind: str  # "1x2" / "double_chance" / "over_1_5"
    fixture: PublicFixtureOut
    market: str  # registry name, e.g. "Double Chance"
    selection: str  # registry name, e.g. "Home/Draw"
    label: str  # plain-English, e.g. "Arsenal or draw"
    probability: float  # as published, frozen when the set was chosen
    # upcoming / live / won / lost / void -- the set stays up through the
    # matches, so its picks are seen to win or lose.
    status: str = "upcoming"
    home_score: int | None = None
    away_score: int | None = None


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


class PaymentNoticeIn(BaseModel):
    # A mobile-money transaction ID or similar -- recorded for the admin to
    # match against their own records, never processed here.
    reference: str = Field(max_length=120)


class AIProductOut(BaseModel):
    key: str
    label: str
    amount: float
    currency: str


class PaymentOptionsOut(BaseModel):
    """What the Access page should offer: paying for access time (``enabled``,
    with its price), and any AI Generation extras the account can buy."""

    enabled: bool
    amount: float = 0
    days: int = 0
    currency: str = ""
    ai_products: list[AIProductOut] = []


class CheckoutIn(BaseModel):
    # "access" (the default) or an AI Generation product key.
    product: str = Field(default="access", max_length=32)


class AIGenerationQuotaOut(BaseModel):
    # None = unlimited (a Super Admin, or a bought day pass is running).
    daily_limit: int | None
    remaining_today: int | None
    credits: int
    unlimited_until: dt.datetime | None = None
    # When the next run frees up, once the daily allowance is used up.
    next_free_at: dt.datetime | None = None


class CheckoutOut(BaseModel):
    reference: str
    checkout_url: str


class PaymentStatusOut(BaseModel):
    reference: str
    product: str = "access"
    # pending / fulfilling / completed / cancelled / failed
    status: str
    access_expires_at: dt.datetime | None = None


class AdminPaymentOut(BaseModel):
    id: int
    user_id: int | None
    user_email: str | None
    reference: str
    amount: float
    access_days: int
    status: str
    created_at: dt.datetime
    completed_at: dt.datetime | None = None


class AdminMessageIn(BaseModel):
    """A direct email from an admin to one or more registered accounts."""

    recipients: list[str] = Field(min_length=1, max_length=25)
    subject: str = Field(min_length=1, max_length=150)
    message: str = Field(min_length=1, max_length=5000)


class AdminMessageResultOut(BaseModel):
    email: str
    sent: bool
    error: str | None = None


class AdminMessageOut(BaseModel):
    results: list[AdminMessageResultOut]
    # Addresses with no registered account -- never emailed.
    not_registered: list[str] = []


class AccessRedeemIn(BaseModel):
    code: str = Field(max_length=64)


class AccessStatusOut(BaseModel):
    has_access: bool
    status: str  # "active" / "expired" / "none" / "unlimited" (superadmin)
    activated_at: dt.datetime | None = None
    expires_at: dt.datetime | None = None


class RevokeCodeIn(BaseModel):
    reason: str | None = None


class RevokeGrantIn(BaseModel):
    reason: str | None = None


class ExtendGrantIn(BaseModel):
    additional_days: int


class SetAIGenerationLimitIn(BaseModel):
    """None clears the override, falling back to the global
    ai_generation_daily_limit setting."""

    daily_limit: int | None = None


class LiveEventIn(BaseModel):
    minute: int = Field(ge=0, le=130)
    score_home: int = Field(ge=0, le=30)
    score_away: int = Field(ge=0, le=30)
    trigger_event: str = Field(max_length=32)


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


class BettingSiteCheckOut(BaseModel):
    name: str
    url: str
    reachable: bool
    status_code: int | None = None
    final_url: str | None = None
    elapsed_ms: int | None = None
    note: str


class BrandingOut(BaseModel):
    """Public site identity and default look, needed before anyone logs in."""

    site_name: str
    site_tagline: str
    default_theme: str
    default_accent: str
    registration_open: bool
    contact_whatsapp: str
    # "" means no price is set -- every consumer falls back to the generic
    # "arrange payment externally" wording with no amount in that case.
    pricing_label: str
    # Which Markets page coupon tab (match_result/double_chance/btts/
    # draw_no_bet/other) opens by default -- see app_settings.py.
    default_market_tab: str
    # Whether entering a referral code at signup currently earns the bonus
    # below -- the Register page only shows the referral field and its
    # "you get N bonus days" copy when this is true.
    referral_enabled: bool
    referral_bonus_days: int
    # Free trial on offer to a new signup, so the landing page can lead
    # with it instead of reading like a paywall. 0 when trials are off.
    trial_days: int = 0
    # "about GH₵4 a day" when the price is set as amount + days (see
    # app_settings.price_display), otherwise "".
    price_per_day: str = ""
    # Public WhatsApp Channel, "" when not set.
    whatsapp_channel_url: str = ""


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
    # Alongside probability and data_quality_score, this is what the frontend's
    # shared is_hot_pick() gate checks -- the same three-factor bar as
    # Prediction.model_agreement_score, just carried per outcome here since a
    # market's own probability (not just the match's single global outcome)
    # decides whether that specific pick clears it.
    model_agreement_score: float
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
    data_quality_score: float
    model_agreement_score: float


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
    # Recomputed from the match's latest Prediction at read time while
    # result is "pending" -- the frozen probability_at_pick snapshot once
    # settled. See FeaturedPick's docstring for why.
    probability: float
    note: str | None
    # pending | won | lost | unresolved -- see app.pick_settlement.
    result: str
    settled_at: dt.datetime | None
    created_at: dt.datetime
    expires_at: dt.datetime
    # True for every Guda Pick but the one free slot a non-premium viewer
    # gets (see routes_predictions.guda_picks) -- market/selection/probability/
    # note are blanked ("" / 0.0 / None) when locked, since this is the one
    # response field that decides whether the frontend may show them at all.
    # Always False for a premium viewer or superadmin.
    locked: bool = False
    # An admin's choice to include this settled pick in the public track
    # record -- see FeaturedPick.featured_public.
    featured_public: bool = False
    # Whether featured_public can still be changed (pending, before
    # kickoff) -- see app.track_record.
    track_record_open: bool = False


class FeaturedPickResultIn(BaseModel):
    """A Super Admin's manual settlement override for a Guda Pick -- same
    reasoning as AdminPickResultIn."""

    result: str  # pending | won | lost | unresolved


class FeaturedPublicIn(BaseModel):
    """Whether to include this settled pick in the public track record --
    see AdminPick.featured_public / FeaturedPick.featured_public."""

    featured_public: bool


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
    # "won" | "lost" | "unresolved" once AdminPickOut.result leaves
    # "pending"; null on every still-live leg. See app.pick_settlement.
    result: str | None = None


class AdminPickResultIn(BaseModel):
    """A Super Admin's manual settlement override -- the fallback for a leg
    on a market app.outcomes.grading doesn't auto-grade (result stuck at
    "unresolved"), or to correct a mistake. Setting "pending" un-freezes the
    pick so it's re-resolved live and re-graded again at the next settle."""

    result: str  # pending | won | lost | unresolved


class AdminPickOut(BaseModel):
    id: int
    legs: list[AdminPickLegOut]
    # pending | won | lost | unresolved -- see app.pick_settlement. Frozen
    # once it leaves "pending": the legs above then come from what was
    # actually picked and what actually happened, not a live re-resolve.
    result: str
    settled_at: dt.datetime | None
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
    # True for every pick but the one free slot a non-premium viewer gets
    # across Admin Picks/This Week's Picks/Random Picks combined (see
    # routes_predictions.admin_picks) -- legs is [] and combined_odds/
    # combined_probability are blanked (None / 0.0) when locked, so leg_count
    # is what lets the frontend still say "10-leg slip" without naming a
    # single match, market or selection. Always False for a premium viewer
    # or superadmin.
    locked: bool = False
    leg_count: int = 0
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
    # An admin's choice to include this settled pick in the public track
    # record -- see AdminPick.featured_public.
    featured_public: bool = False
    # Whether featured_public can still be changed -- see app.track_record.
    track_record_open: bool = False


# --- Booking codes -----------------------------------------------------


class BetCodePickIn(BaseModel):
    """An explicit (match, market, selection) -- no search, no criteria,
    just "resolve exactly this". What a chat picks list or the Markets
    page's own shortlist sends over; see app.betcode.selection's
    resolve_legs_unpriced (POST /resolve) and select_legs_by_confidence
    (POST /suggest)."""

    match_id: int
    market: str
    selection: str


class ExcludedLegIn(BaseModel):
    match_id: int
    market: str


class SuggestCriteriaIn(BaseModel):
    """What POST /api/betcodes/suggest searches with -- no target price, no
    bookmaker: the result is meant to be booked directly with a betting site
    (POST /picks), which prices the slip itself."""

    min_probability: float | None = None
    max_legs: int | None = None
    markets: list[str] = []
    # Empty = every league this deployment has data for; non-empty = any one
    # of these (a match only has one league, so this is a union, not an
    # intersection) -- lets AI Generation search several leagues at once
    # without also having to mean "all of them".
    leagues: list[str] = []
    days_ahead: int = 7
    # A calendar pick (frontend/src/components/FixtureCalendar.tsx) takes
    # over from days_ahead when set -- see
    # app.betcode.selection._match_window for exactly how. date_from alone
    # means that one day, not "from then on"; both together is a range.
    date_from: dt.date | None = None
    date_to: dt.date | None = None
    # Default (false): each match contributes its single strongest
    # qualifying outcome, regardless of which wanted market that is -- a
    # match where Match Result clears 85% never shows its 60% BTTS leg
    # instead, even if BTTS was explicitly selected. True: every selected
    # market gets its own leg, each on a different match, whenever the data
    # allows -- for someone who ticked several markets specifically to see
    # all of them on the slip. See
    # app.betcode.selection.select_legs_featuring_every_market.
    feature_all_markets: bool = False
    # Matches to leave out of the search -- AI Generation's Refresh button
    # sends back whatever the previous search returned, so running the
    # exact same criteria again surfaces a genuinely different combination
    # instead of the identical, deterministic result safest-first search
    # always lands on for unchanged criteria over unchanged data.
    exclude_match_ids: list[int] = []
    # The finer-grained form Refresh actually sends: (match, market) pairs
    # already shown. The match stays eligible, so a refresh can move a game to
    # a different market instead of only swapping games.
    exclude_legs: list[ExcludedLegIn] = []


class SuggestRandomCriteriaIn(BaseModel):
    """What POST /api/betcodes/suggest-random searches with -- same no-price,
    no-bookmaker reasoning as SuggestCriteriaIn, but legs are drawn in
    random order (app.betcode.selection.select_random_legs) rather than
    safest-first, landing on a slip somewhere between min_legs and max_legs
    instead of one fixed count. The route clamps both to [10, 20]
    regardless of what's sent here -- a wider range is
    scripts/generate_random_picks.py's job, not a member-facing request."""

    min_probability: float | None = None
    min_legs: int = 10
    max_legs: int = 20
    markets: list[str] = []
    leagues: list[str] = []
    days_ahead: int = 7
    date_from: dt.date | None = None
    date_to: dt.date | None = None
    # See SuggestCriteriaIn's own field -- threaded through here too so
    # Refresh works the same way for a random draw (already varies on its
    # own, but this still keeps it from re-surfacing a match the visible
    # slip already has a leg from).
    exclude_match_ids: list[int] = []
    exclude_legs: list[ExcludedLegIn] = []


class ResolvePicksIn(BaseModel):
    picks: list[BetCodePickIn]


class BettingSiteOut(BaseModel):
    key: str
    name: str
    connected: bool


class SiteCodeOut(BaseModel):
    site: str
    name: str
    status: str  # code_ready | not_connected | error
    code: str | None = None
    link: str | None = None
    message: str | None = None
    unavailable_match_ids: list[int] = []
    # match_id -> why that pick isn't on the site's slip.
    unavailable_reasons: dict[int, str] = {}


class PicksBookingIn(BaseModel):
    """Games and outcomes a member picked themselves, to book as-is on the
    chosen sites. No price is needed: each site prices its own slip."""

    picks: list[BetCodePickIn]
    sites: list[str]


class PickedLegOut(BaseModel):
    match_id: int
    league: str
    home_team: str
    away_team: str
    kickoff: dt.datetime
    market: str
    selection: str
    model_probability: float
    # "won" | "lost" | "unresolved" once the owning slip has settled, null
    # while still pending -- only ever populated on a "my codes" history
    # entry (GET /api/betcodes/mine), never on /suggest, /resolve or /picks,
    # which are always freshly booked and so always still pending.
    result: str | None = None


class PicksBookingOut(BaseModel):
    # The picks that were booked -- after the same checks AI Generation
    # makes (a real upcoming match, an outcome its prediction offers, one
    # pick per match). Anything left out is explained in ``warnings``.
    legs: list[PickedLegOut]
    site_codes: list[SiteCodeOut]
    warnings: list[str]


class SuggestedPicksOut(BaseModel):
    """Shared response shape for /suggest (criteria in, legs the engine
    picked) and /resolve (explicit picks in, the same picks resolved
    against each match's current prediction) -- neither prices anything or
    combines toward a target; both are previews of what /picks would book."""

    legs: list[PickedLegOut]
    combined_probability: float
    candidates_considered: int
    warnings: list[str]


class MyBookingSlipOut(BaseModel):
    """One entry in a member's own "my codes" results history -- every code
    they've ever generated through /api/betcodes/picks, with what actually
    happened once it settles. See BookingSlip and app.pick_settlement."""

    id: int
    legs: list[PickedLegOut]
    combined_probability: float
    site_codes: list[SiteCodeOut]
    result: str  # pending | won | lost | unresolved
    created_at: dt.datetime
    settled_at: dt.datetime | None


class TrackRecordEntryOut(BaseModel):
    """One settled, featured pick shown with its real teams/market/selection
    on the public track record -- a deliberate, admin-curated exception to
    this router's usual "stays behind the login" rule, same reasoning as
    PublicTeaserPickOut: a visitor deciding whether to trust this platform
    needs to see what was actually called and what actually happened, not
    just an aggregate percentage. Still only ever a pick an admin chose to
    feature, never every settled one."""

    kind: str  # "admin_pick" | "guda_pick"
    label: str | None = None
    legs: list[AdminPickLegOut]
    result: str  # won | lost | unresolved
    settled_at: dt.datetime | None


class AdminPickTrackRecordOut(BaseModel):
    """The platform's public record for its curated Admin Picks + Guda
    Picks. ``picks`` shows each featured, settled one's real legs --
    see TrackRecordEntryOut for why this is a deliberate exception to the
    rest of this file's "stays behind the login" rule -- capped to the most
    recent MAX_TRACK_RECORD_ENTRIES; the aggregate counts below always cover
    every featured settled pick regardless of that cap. Counts only picks an
    admin has chosen to feature here (AdminPick.featured_public /
    FeaturedPick.featured_public), not every settled pick. See
    routes_public.admin_pick_track_record."""

    has_data: bool
    settled_count: int = 0
    won: int = 0
    lost: int = 0
    unresolved: int = 0
    hit_rate: float = 0.0  # won / (won + lost); unresolved excluded
    picks: list[TrackRecordEntryOut] = []


class PublicTeaserPickOut(BaseModel):
    """The one showcase combo shown on the public, unauthenticated Welcome
    page -- unlike every other pick type in this file, its legs (team names,
    market, selection) are shown to everyone, logged in or not: a deliberate
    marketing exception to this platform's usual "selections stay behind the
    login" rule (see AdminPickTrackRecordOut above, which is the rule).
    has_data=False when no admin has set one, or the one that was set has
    gone stale -- a leg's match has kicked off or stopped resolving, same
    "drops out entirely" rule AdminPick's own docstring describes. Legs are
    always re-priced live, never settled; see AdminPick.public_teaser."""

    has_data: bool
    legs: list[AdminPickLegOut] = []
    combined_odds: float = 0.0
    combined_probability: float = 0.0
    label: str | None = None
    created_at: dt.datetime | None = None


PublicFixtureDetailOut.model_rebuild()


class WinningSlipIn(BaseModel):
    # The image as a data URL or bare base64 -- JPEG, PNG or WebP, at most
    # MAX_WINNING_SLIP_BYTES once decoded (routes_admin checks both).
    image_data: str = Field(max_length=4_000_000)
    caption: str = Field(default="", max_length=200)
    won_on: dt.date | None = None
    width: int | None = Field(default=None, ge=1, le=10_000)
    height: int | None = Field(default=None, ge=1, le=10_000)


class WinningSlipUpdateIn(BaseModel):
    caption: str | None = Field(default=None, max_length=200)
    won_on: dt.date | None = None
    published: bool | None = None


class WinningSlipOut(BaseModel):
    id: int
    caption: str
    won_on: dt.date | None = None
    # Relative to the API (GET /api/public/winning-slips/{id}/image).
    image_path: str
    width: int | None = None
    height: int | None = None
    published: bool
    # Shared by a member rather than posted by the team.
    from_member: bool = False
    # "live" / "pending" (waiting for an admin) / "not_shown" (hidden or
    # not approved).
    status: str = "live"
    created_at: dt.datetime


class AdminWinningSlipOut(WinningSlipOut):
    # Who shared it -- admin review only, never on the public list.
    submitted_by_email: str | None = None
    submitted_by_name: str | None = None


class WinningSlipImageDataOut(BaseModel):
    # A data: URL, so a not-yet-public image can be shown to the admin
    # reviewing it or the member who shared it.
    data_url: str


class LandingOut(BaseModel):
    """Everything the public home page reads, in one response -- it used to
    make seven separate calls to a backend that can be asleep, so each
    section filled in on its own schedule (or not at all) on a cold start."""

    branding: BrandingOut
    stats: PublicStatsOut
    track_record: TrackRecordOut
    admin_pick_record: AdminPickTrackRecordOut
    fixtures: list[PublicFixtureOut]
    free_picks: list[PublicFreePickOut]
    winning_slips: list[WinningSlipOut] = []
    teaser_pick: PublicTeaserPickOut


# --- news banner -------------------------------------------------------------


class BannerSlideOut(BaseModel):
    id: str
    kind: str  # "news" | "ad"
    headline: str
    label: str  # publisher, or the sponsor's name
    url: str
    body: str | None = None
    cta: str | None = None
    published_at: dt.datetime | None = None


class BannerOut(BaseModel):
    slides: list[BannerSlideOut]


class BannerItemIn(BaseModel):
    sponsor: str = Field(min_length=1, max_length=80)
    headline: str = Field(min_length=1, max_length=140)
    body: str | None = Field(default=None, max_length=200)
    cta_label: str = Field(default="Learn more", min_length=1, max_length=40)
    link_url: str = Field(max_length=500)
    active: bool = True
    starts_at: dt.datetime | None = None
    ends_at: dt.datetime | None = None
    sort_order: int = Field(default=0, ge=-1000, le=1000)


class BannerItemUpdateIn(BaseModel):
    sponsor: str | None = Field(default=None, min_length=1, max_length=80)
    headline: str | None = Field(default=None, min_length=1, max_length=140)
    body: str | None = Field(default=None, max_length=200)
    cta_label: str | None = Field(default=None, min_length=1, max_length=40)
    link_url: str | None = Field(default=None, max_length=500)
    active: bool | None = None
    starts_at: dt.datetime | None = None
    ends_at: dt.datetime | None = None
    sort_order: int | None = Field(default=None, ge=-1000, le=1000)
    # Sent explicitly to remove a start/end date (None means "leave alone").
    clear_starts_at: bool = False
    clear_ends_at: bool = False


class BannerItemOut(BaseModel):
    id: int
    sponsor: str
    headline: str
    body: str | None = None
    cta_label: str
    link_url: str
    active: bool
    starts_at: dt.datetime | None = None
    ends_at: dt.datetime | None = None
    sort_order: int
    impressions: int
    clicks: int
    created_at: dt.datetime
    # Derived: whether it is showing to members right now.
    live: bool
