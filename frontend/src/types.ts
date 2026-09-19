export interface Team {
  id: number;
  name: string;
  league: string;
  country: string | null;
}

export interface MatchSummary {
  id: number;
  league: string;
  season: string;
  date: string;
  status: string;
  home_team: Team;
  away_team: Team;
  home_score: number | null;
  away_score: number | null;
  /** Whether this match is genuinely, currently live -- unlike raw
   * `status === "LIVE"`, this already accounts for a simulated event
   * pushed from the match's own Live tab, or a real fixture stuck from a
   * missed sync poll. Always prefer this over checking status directly. */
  is_live: boolean;
}

export interface GlobalOutcome {
  market: string;
  selection: string;
  probability: number;
}

export interface Prediction {
  match_id: number;
  created_at: string;
  model_version: string;
  home_win: number;
  draw: number;
  away_win: number;
  over_probabilities: Record<string, number>;
  btts_yes: number;
  btts_no: number;
  correct_score_probabilities: Record<string, number>;
  most_likely_score: string;
  most_likely_score_probability: number;
  global_outcome: GlobalOutcome;
  /** The next-best outcomes after the headline pick, each with its own
   * probability -- never combined with it or with each other. Never
   * includes Double Chance (see the backend for why) and shorter than 2
   * for a thin-data match. */
  also_likely: GlobalOutcome[];
  confidence: "HIGH" | "MEDIUM" | "LOW";
  data_quality_score: number;
  model_agreement_score: number;
  explanation: { positive: string[]; negative: string[] };
  model_breakdown: Record<string, unknown>;
}

export interface LivePrediction {
  match_id: number;
  created_at: string;
  minute: number;
  score_home: number;
  score_away: number;
  home_win: number;
  draw: number;
  away_win: number;
  over_probabilities: Record<string, number>;
  btts_yes: number;
  global_outcome: GlobalOutcome;
  trigger_event: string;
}

export type UserRole = "user" | "superadmin";
export type UserStatus = "active" | "suspended";

export interface User {
  id: number;
  email: string;
  name: string;
  role: UserRole;
  status: UserStatus;
  theme: string | null;
  accent_profile: string | null;
  created_at: string;
}

export interface MatchHistoryEntry {
  match: MatchSummary;
  viewed_at: string;
}

export interface AdminUser extends User {
  access_status: "active" | "expired" | "none";
  access_expires_at: string | null;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  user: User;
}

export interface ModelBreakdown {
  elo?: { home_win: number; draw: number; away_win: number; elo_diff: number };
  poisson?: { home_win: number; draw: number; away_win: number; lambda_home: number; lambda_away: number };
  ml?: { H: number; D: number; A: number } | null;
}

export interface TeamForm {
  matches_played: number;
  points_per_game: number;
  goals_scored_avg: number;
  goals_conceded_avg: number;
  home_goals_scored_avg: number;
  home_goals_conceded_avg: number;
  away_goals_scored_avg: number;
  away_goals_conceded_avg: number;
  clean_sheet_rate: number;
  rest_days: number;
  recent_results: string[];
}

export interface MatchStatistics {
  home_team: string;
  away_team: string;
  home_form: TeamForm;
  away_form: TeamForm;
}

export interface HeadToHeadMatch {
  date: string;
  league: string;
  home_team: string;
  away_team: string;
  home_score: number;
  away_score: number;
}

export interface UnavailableResource {
  status: "unavailable";
  reason: string;
}

// --- AI assistant chat ----------------------------------------------------

export interface ChatSource {
  kind: "match" | "team" | "page";
  label: string;
  ref: string | number | null;
}

export interface ChatAnswer {
  id: number | null;
  text: string;
  intent: string;
  sources: ChatSource[];
  suggestions: string[];
  includes_probability: boolean;
  caveat: string | null;
}

export interface ChatMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  intent: string | null;
  context_match_id: number | null;
  sources: ChatSource[];
  suggestions: string[];
  created_at: string;
}

// --- Public landing-page data --------------------------------------------

export interface PublicStats {
  matches_analyzed: number;
  teams_tracked: number;
  leagues_covered: number;
  predictions_generated: number;
  upcoming_fixtures: number;
  active_members: number;
  league_names: string[];
  markets_per_match: number;
}

export interface PublicAccuracy {
  has_data: boolean;
  model_version: string | null;
  computed_at: string | null;
  split: string | null;
  leagues: string[];
  accuracy: number | null;
  log_loss: number | null;
  brier_score: number | null;
  matches_evaluated: number | null;
}

export interface PublicFixture {
  league: string;
  kickoff: string;
  home_team: string;
  away_team: string;
  has_prediction: boolean;
  confidence: string | null;
}

export interface AccountStatus {
  status: "no_access" | "active" | "suspended";
  message: string;
  submitted_at: string | null;
}

// --- Access codes ----------------------------------------------------------

export interface AccessCode {
  id: number;
  code: string;
  status: "active" | "revoked" | "exhausted" | "expired";
  duration_days: number;
  code_expires_at: string | null;
  redemption_limit: number;
  redemption_count: number;
  assigned_user_id: number | null;
  assigned_email: string | null;
  created_by_user_id: number;
  created_at: string;
  revoked_at: string | null;
  revoked_reason: string | null;
  notes: string | null;
  /** Only on the create response: whether the code was emailed, and why not. */
  emailed?: boolean;
  email_error?: string | null;
}

export interface AccessGrant {
  id: number;
  access_code_id: number;
  status: "active" | "revoked" | "expired";
  activated_at: string;
  expires_at: string;
  revoked_at: string | null;
  revoked_reason: string | null;
}

export interface AccessStatus {
  has_access: boolean;
  status: "active" | "expired" | "none";
  activated_at: string | null;
  expires_at: string | null;
}

export interface SiteNotice {
  enabled: boolean;
  message: string;
}

/** The free tier's shape -- deliberately thinner than Prediction: a
 * headline pick, not the full market breakdown. */
export interface FreePick {
  match_id: number;
  league: string;
  home_team: string;
  away_team: string;
  kickoff: string;
  home_win: number;
  draw: number;
  away_win: number;
  selection: string;
  probability: number;
  confidence: "HIGH" | "MEDIUM" | "LOW";
}

/** An outcome a Super Admin chose to promote -- probability is always the
 * match's current recomputed value, never a snapshot from when it was
 * featured. */
export interface FeaturedPick {
  id: number;
  match: MatchSummary;
  market: string;
  selection: string;
  probability: number;
  note: string | null;
  created_at: string;
  expires_at: string;
}

// --- Admin ---------------------------------------------------------------

export interface AdminOverview {
  active_users: number;
  suspended_users: number;
  superadmins: number;
  total_users: number;
  users_without_access: number;
  active_access_grants: number;
  matches_analyzed: number;
  predictions_generated: number;
  upcoming_fixtures: number;
  chat_messages: number;
}

export interface AuditLogEntry {
  id: number;
  actor_email: string | null;
  action: string;
  target_user_id: number | null;
  detail: Record<string, unknown> | null;
  created_at: string;
}

// --- Super Admin settings --------------------------------------------------

export interface SettingSpec {
  key: string;
  kind: "str" | "int" | "float" | "bool" | "choice";
  group: string;
  label: string;
  help: string;
  secret: boolean;
  choices: string[];
  minimum: number | null;
  maximum: number | null;
}

export interface SettingsPayload {
  values: Record<string, string | number | boolean>;
  /** Secrets are never in `values` -- this only says whether one exists. */
  secrets_set: Record<string, boolean>;
  /** Keys with a saved override, as opposed to the deployment default. */
  overridden: string[];
  groups: Record<string, string>;
  specs: SettingSpec[];
}

export interface TestEmailResult {
  sent: boolean;
  detail: string;
}

export interface SystemStatus {
  database_reachable: boolean;
  matches: number;
  predictions: number;
  upcoming_fixtures: number;
  leagues: string[];
  users: number;
  active_grants: number;
  unredeemed_codes: number;
  latest_match_date: string | null;
  latest_prediction_at: string | null;
  model_files: string[];
  calibrator_files: number;
  models_built_at: string | null;
  email_configured: boolean;
  settings_overridden: number;
}

export interface Branding {
  site_name: string;
  site_tagline: string;
  default_theme: string;
  default_accent: string;
  registration_open: boolean;
  contact_whatsapp: string;
}

// --- Outcome browser -------------------------------------------------------

export interface BettingOutcome {
  match_id: number;
  league: string;
  home_team: string;
  away_team: string;
  kickoff: string;
  market: string;
  selection: string;
  probability: number;
  confidence: "HIGH" | "MEDIUM" | "LOW";
  data_quality_score: number;
  /** Selections sharing a group are mutually exclusive and sum to ~1. */
  group: string;
  definition: string;
}

export interface MarketSummary {
  market: string;
  group: string;
  selections: string[];
  outcomes: number;
  mutually_exclusive: boolean;
}

export interface LeagueOutcomes {
  league: string;
  matches: number;
  outcomes: BettingOutcome[];
}

export interface OutcomesResponse {
  markets: MarketSummary[];
  leagues: LeagueOutcomes[];
  total_outcomes: number;
  total_matches: number;
  days_ahead: number;
}

// --- Booking codes -------------------------------------------------------

export interface BetCodeCriteria {
  /** Who the generated code is for -- has no bearing on which bookmaker's
   * prices get used, see price_bookmaker and BetCodeLeg.priced_by. */
  bookmaker: string;
  target_odds: number;
  markets: string[];
  min_probability?: number | null;
  max_legs?: number | null;
  league?: string | null;
  days_ahead: number;
  /** Whose captured prices to price legs from. Omitted/null = any bookmaker
   * this project has a real quote from. */
  price_bookmaker?: string | null;
}

export interface BetCodeLeg {
  match_id: number;
  league: string;
  home_team: string;
  away_team: string;
  kickoff: string;
  market: string;
  selection: string;
  model_probability: number;
  decimal_odds: number;
  /** Which bookmaker's stored quote this price came from -- not necessarily
   * the bookmaker the slip is being generated for. */
  priced_by: string;
}

export interface BetCodePreview {
  legs: BetCodeLeg[];
  combined_odds: number;
  combined_probability: number;
  target_odds: number;
  met_target: boolean;
  candidates_considered: number;
  warnings: string[];
}

export type BetCodeStatus = "selected" | "code_ready" | "provider_unavailable" | "provider_error";

export interface BetCode {
  id: number;
  created_at: string;
  bookmaker: string;
  legs: BetCodeLeg[];
  combined_odds: number;
  combined_probability: number;
  expires_at: string;
  provider: string;
  status: BetCodeStatus;
  booking_code: string | null;
  deep_link: string | null;
  provider_message: string | null;
}
