import type {
  HeadToHeadMatch,
  LivePrediction,
  MatchStatistics,
  MatchSummary,
  Prediction,
  Team,
  TeamForm,
  UnavailableResource,
} from "./types";

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

export function fetchMostLikely(league?: string, daysAhead = 3, limit = 10): Promise<Prediction[]> {
  const params = new URLSearchParams({ days_ahead: String(daysAhead), limit: String(limit) });
  if (league) params.set("league", league);
  return get(`/api/predictions/most-likely?${params.toString()}`);
}

export function fetchHighConfidence(league?: string, daysAhead = 3): Promise<Prediction[]> {
  const params = new URLSearchParams({ days_ahead: String(daysAhead) });
  if (league) params.set("league", league);
  return get(`/api/predictions/high-confidence?${params.toString()}`);
}

export function fetchMatch(matchId: number): Promise<MatchSummary> {
  return get(`/api/matches/${matchId}`);
}

export function fetchPrediction(matchId: number, forceRefresh = false): Promise<Prediction> {
  return get(`/api/matches/${matchId}/prediction${forceRefresh ? "?force_refresh=true" : ""}`);
}

export function fetchPredictionHistory(matchId: number): Promise<Prediction[]> {
  return get(`/api/matches/${matchId}/prediction-history`);
}

export function fetchLive(matchId: number): Promise<LivePrediction[]> {
  return get(`/api/matches/${matchId}/live`);
}

export function fetchMatches(params: { league?: string; status?: string; teamId?: number; date?: string } = {}): Promise<MatchSummary[]> {
  const search = new URLSearchParams();
  if (params.league) search.set("league", params.league);
  if (params.status) search.set("status", params.status);
  if (params.teamId) search.set("team_id", String(params.teamId));
  if (params.date) search.set("date", params.date);
  const query = search.toString();
  return get(`/api/matches${query ? `?${query}` : ""}`);
}

export function fetchStatistics(matchId: number): Promise<MatchStatistics> {
  return get(`/api/matches/${matchId}/statistics`);
}

export function fetchLineup(matchId: number): Promise<UnavailableResource> {
  return get(`/api/matches/${matchId}/lineup`);
}

export function fetchNews(matchId: number): Promise<UnavailableResource> {
  return get(`/api/matches/${matchId}/news`);
}

export function fetchTeams(league?: string): Promise<Team[]> {
  const query = league ? `?league=${encodeURIComponent(league)}` : "";
  return get(`/api/teams${query}`);
}

export function fetchTeam(teamId: number): Promise<Team> {
  return get(`/api/teams/${teamId}`);
}

export function fetchTeamForm(teamId: number): Promise<TeamForm> {
  return get(`/api/teams/${teamId}/form`);
}

export function fetchHeadToHead(teamId: number, opponentId: number, limit = 10): Promise<HeadToHeadMatch[]> {
  return get(`/api/teams/${teamId}/head-to-head/${opponentId}?limit=${limit}`);
}

export async function postLiveEvent(
  matchId: number,
  payload: { minute: number; score_home: number; score_away: number; trigger_event: string },
): Promise<LivePrediction> {
  const response = await fetch(`${API_URL}/api/matches/${matchId}/live-event`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(`live-event failed: ${response.status} ${response.statusText}`);
  }
  return response.json();
}

export type { TeamForm } from "./types";
