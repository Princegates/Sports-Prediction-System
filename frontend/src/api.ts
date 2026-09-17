import type { LivePrediction, MatchSummary, Prediction } from "./types";

const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

async function get<T>(path: string): Promise<T> {
  const response = await fetch(`${API_URL}${path}`);
  if (!response.ok) {
    throw new Error(`${path} failed: ${response.status} ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

export function fetchTodaysPredictions(league?: string): Promise<Prediction[]> {
  const query = league ? `?league=${encodeURIComponent(league)}` : "";
  return get(`/api/predictions/today${query}`);
}

export function fetchMostLikely(league?: string, daysAhead = 3): Promise<Prediction[]> {
  const params = new URLSearchParams({ days_ahead: String(daysAhead) });
  if (league) params.set("league", league);
  return get(`/api/predictions/most-likely?${params.toString()}`);
}

export function fetchMatch(matchId: number): Promise<MatchSummary> {
  return get(`/api/matches/${matchId}`);
}

export function fetchPrediction(matchId: number): Promise<Prediction> {
  return get(`/api/matches/${matchId}/prediction`);
}

export function fetchLive(matchId: number): Promise<LivePrediction[]> {
  return get(`/api/matches/${matchId}/live`);
}

export function fetchMatches(league?: string): Promise<MatchSummary[]> {
  const query = league ? `?league=${encodeURIComponent(league)}` : "";
  return get(`/api/matches${query}`);
}

export interface TeamForm {
  matches_played: number;
  points_per_game: number;
  goals_scored_avg: number;
  goals_conceded_avg: number;
  clean_sheet_rate: number;
  rest_days: number;
  recent_results: string[];
}

export function fetchStatistics(matchId: number): Promise<{ home_team: string; away_team: string; home_form: TeamForm; away_form: TeamForm }> {
  return get(`/api/matches/${matchId}/statistics`);
}
