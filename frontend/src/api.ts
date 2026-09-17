import type {
  AdminUser,
  HeadToHeadMatch,
  LivePrediction,
  MatchStatistics,
  MatchSummary,
  Prediction,
  Team,
  TeamForm,
  TokenResponse,
  UnavailableResource,
  User,
} from "./types";

const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
export const TOKEN_STORAGE_KEY = "auth_token";

export function getStoredToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_STORAGE_KEY);
  } catch {
    return null;
  }
}

export function storeToken(token: string): void {
  try {
    localStorage.setItem(TOKEN_STORAGE_KEY, token);
  } catch {
    // storage disabled -- session just won't persist across reloads
  }
}

export function clearStoredToken(): void {
  try {
    localStorage.removeItem(TOKEN_STORAGE_KEY);
  } catch {
    // ignore
  }
}

/** Fired when a request comes back 401 so AuthContext can clear state and
 * redirect to /login, even for calls made outside of a component. */
const AUTH_LOGOUT_EVENT = "auth:logout";

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getStoredToken();
  const headers: Record<string, string> = { ...(options.headers as Record<string, string>) };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (options.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";

  const response = await fetch(`${API_URL}${path}`, { ...options, headers });

  if (response.status === 401) {
    clearStoredToken();
    window.dispatchEvent(new Event(AUTH_LOGOUT_EVENT));
    throw new Error("Session expired -- please log in again.");
  }
  if (!response.ok) {
    const detail = await response.json().catch(() => null);
    throw new Error(detail?.detail ?? `${path} failed: ${response.status} ${response.statusText}`);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

function get<T>(path: string): Promise<T> {
  return request(path);
}

function post<T>(path: string, body: unknown): Promise<T> {
  return request(path, { method: "POST", body: JSON.stringify(body) });
}

export function onAuthLogout(handler: () => void): () => void {
  window.addEventListener(AUTH_LOGOUT_EVENT, handler);
  return () => window.removeEventListener(AUTH_LOGOUT_EVENT, handler);
}

// --- Auth ------------------------------------------------------------

export function registerAccount(payload: { email: string; name: string; password: string; payment_reference?: string }) {
  return post<{ message: string; user: User }>("/api/auth/register", payload);
}

export function login(email: string, password: string): Promise<TokenResponse> {
  return post("/api/auth/login", { email, password });
}

export function fetchMe(): Promise<User> {
  return get("/api/auth/me");
}

// --- Admin -------------------------------------------------------------

export function fetchAdminUsers(status?: string): Promise<AdminUser[]> {
  return get(`/api/admin/users${status ? `?status=${status}` : ""}`);
}

export function approveUser(userId: number, paymentReference?: string): Promise<AdminUser> {
  return post(`/api/admin/users/${userId}/approve`, { payment_reference: paymentReference });
}

export function suspendUser(userId: number): Promise<AdminUser> {
  return post(`/api/admin/users/${userId}/suspend`, {});
}

export function promoteUser(userId: number): Promise<AdminUser> {
  return post(`/api/admin/users/${userId}/promote`, {});
}

export function demoteUser(userId: number): Promise<AdminUser> {
  return post(`/api/admin/users/${userId}/demote`, {});
}

// --- Predictions / matches / teams --------------------------------------

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

export function postLiveEvent(
  matchId: number,
  payload: { minute: number; score_home: number; score_away: number; trigger_event: string },
): Promise<LivePrediction> {
  return post(`/api/matches/${matchId}/live-event`, payload);
}

export type { TeamForm } from "./types";
