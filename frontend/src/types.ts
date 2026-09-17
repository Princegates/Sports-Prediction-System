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
