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
export type UserStatus = "pending" | "active" | "suspended";

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
  payment_reference: string | null;
  approved_by_user_id: number | null;
  approved_at: string | null;
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
