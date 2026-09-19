import type {
  OutcomesResponse,
  Branding,
  SettingsPayload,
  SystemStatus,
  TestEmailResult,
  AccessCode,
  AccessGrant,
  AccessStatus,
  AccountStatus,
  AdminOverview,
  AdminUser,
  AuditLogEntry,
  ChatAnswer,
  ChatMessage,
  FreePick,
  HeadToHeadMatch,
  LivePrediction,
  MatchHistoryEntry,
  MatchStatistics,
  MatchSummary,
  Prediction,
  PublicAccuracy,
  PublicFixture,
  PublicStats,
  SiteNotice,
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

function patch<T>(path: string, body: unknown): Promise<T> {
  return request(path, { method: "PATCH", body: JSON.stringify(body) });
}

function del<T>(path: string): Promise<T> {
  return request(path, { method: "DELETE" });
}

export function onAuthLogout(handler: () => void): () => void {
  window.addEventListener(AUTH_LOGOUT_EVENT, handler);
  return () => window.removeEventListener(AUTH_LOGOUT_EVENT, handler);
}

// --- Auth ------------------------------------------------------------

export function registerAccount(payload: { email: string; name: string; password: string }) {
  return post<{ message: string; user: User }>("/api/auth/register", payload);
}

export function login(email: string, password: string): Promise<TokenResponse> {
  return post("/api/auth/login", { email, password });
}

export function fetchMe(): Promise<User> {
  return get("/api/auth/me");
}

export function fetchAccountStatus(email: string, password: string): Promise<AccountStatus> {
  return post("/api/auth/status", { email, password });
}

// --- Public (no auth required) -----------------------------------------

export function fetchPublicStats(): Promise<PublicStats> {
  return get("/api/public/stats");
}

export function fetchPublicAccuracy(): Promise<PublicAccuracy> {
  return get("/api/public/accuracy");
}

export function fetchPublicFixtures(daysAhead = 5, limit = 6): Promise<PublicFixture[]> {
  return get(`/api/public/fixtures?days_ahead=${daysAhead}&limit=${limit}`);
}

// --- AI assistant chat --------------------------------------------------

export function sendChatMessage(message: string, contextMatchId?: number | null): Promise<ChatAnswer> {
  return post("/api/chat/message", { message, context_match_id: contextMatchId ?? null });
}

export function fetchChatHistory(limit = 50): Promise<ChatMessage[]> {
  return get(`/api/chat/history?limit=${limit}`);
}

export function clearChatHistory(): Promise<{ deleted: number }> {
  return del("/api/chat/history");
}

interface StreamHandlers {
  onChunk: (text: string) => void;
  onDone: (answer: ChatAnswer) => void;
  onError: (message: string) => void;
}

/**
 * Streams an assistant reply over SSE.
 *
 * `fetch` + a manual parser rather than `EventSource`, because EventSource
 * can only issue GETs and cannot set an Authorization header -- the token
 * would have to go in the query string, where it lands in access logs.
 *
 * Returns an abort function. Calling it stops the client reading, but the
 * answer was already persisted server-side before the first chunk was sent,
 * so an aborted stream still leaves the exchange in the user's history.
 */
export function streamChatMessage(
  message: string,
  contextMatchId: number | null,
  handlers: StreamHandlers,
): () => void {
  const controller = new AbortController();
  const token = getStoredToken();

  (async () => {
    try {
      const response = await fetch(`${API_URL}/api/chat/stream`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ message, context_match_id: contextMatchId }),
        signal: controller.signal,
      });

      if (response.status === 401) {
        clearStoredToken();
        window.dispatchEvent(new Event(AUTH_LOGOUT_EVENT));
        handlers.onError("Session expired -- please log in again.");
        return;
      }
      if (!response.ok || !response.body) {
        const detail = await response.json().catch(() => null);
        handlers.onError(detail?.detail ?? `Chat failed: ${response.status}`);
        return;
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      // SSE frames are separated by a blank line. A frame can arrive split
      // across reads, so anything after the last separator stays buffered
      // until the rest of it turns up.
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const frames = buffer.split("\n\n");
        buffer = frames.pop() ?? "";

        for (const frame of frames) {
          if (!frame.trim()) continue;
          let event = "message";
          const dataLines: string[] = [];
          for (const line of frame.split("\n")) {
            if (line.startsWith("event: ")) event = line.slice(7).trim();
            else if (line.startsWith("data: ")) dataLines.push(line.slice(6));
          }
          if (dataLines.length === 0) continue;

          try {
            const payload = JSON.parse(dataLines.join("\n"));
            if (event === "chunk") handlers.onChunk(payload.text);
            else if (event === "done") handlers.onDone(payload as ChatAnswer);
            else if (event === "error") handlers.onError(payload.detail ?? "Assistant error");
          } catch {
            // A malformed frame shouldn't kill the rest of the stream.
          }
        }
      }
    } catch (err) {
      if ((err as Error)?.name === "AbortError") return;
      handlers.onError(err instanceof Error ? err.message : String(err));
    }
  })();

  return () => controller.abort();
}

export function updatePreferences(payload: { theme?: string; accent_profile?: string }): Promise<User> {
  return patch("/api/auth/preferences", payload);
}

export function updateProfile(name: string): Promise<User> {
  return patch("/api/auth/profile", { name });
}

export function changePassword(currentPassword: string, newPassword: string): Promise<void> {
  return patch("/api/auth/password", { current_password: currentPassword, new_password: newPassword });
}

export function recordMatchView(matchId: number): Promise<void> {
  return request(`/api/auth/history/${matchId}`, { method: "POST" });
}

export function fetchMatchHistory(): Promise<MatchHistoryEntry[]> {
  return get("/api/auth/history");
}

// --- Admin -------------------------------------------------------------

export function fetchAdminUsers(status?: string): Promise<AdminUser[]> {
  return get(`/api/admin/users${status ? `?status=${status}` : ""}`);
}

export function fetchAdminOverview(): Promise<AdminOverview> {
  return get("/api/admin/overview");
}

export function fetchAuditLog(limit = 50): Promise<AuditLogEntry[]> {
  return get(`/api/admin/audit-log?limit=${limit}`);
}

export function reinstateUser(userId: number): Promise<AdminUser> {
  return post(`/api/admin/users/${userId}/reinstate`, {});
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

// --- Access codes --------------------------------------------------------

export function fetchAccessStatus(): Promise<AccessStatus> {
  return get("/api/access/status");
}

/** One headline pick per league -- works for any logged-in account, even
 * one with no redeemed code. See FreePickOut on the backend for why it's
 * deliberately thinner than a full Prediction. */
export function fetchFreePicks(): Promise<FreePick[]> {
  return get("/api/predictions/free-picks");
}

export function redeemAccessCode(code: string): Promise<AccessGrant> {
  return post("/api/access/redeem", { code });
}

export function fetchAccessCodes(): Promise<AccessCode[]> {
  return get("/api/admin/access-codes");
}

export function createAccessCode(payload: {
  duration_days: number;
  code_expires_in_days?: number;
  /** Must belong to an already-registered account -- the code is bound to it. */
  assigned_user_email: string;
  notes?: string;
  /** Email the code to that address. Delivery failure never loses the code. */
  send_email?: boolean;
}): Promise<AccessCode> {
  return post("/api/admin/access-codes", payload);
}

export function revokeAccessCode(codeId: number, reason?: string): Promise<AccessCode> {
  return post(`/api/admin/access-codes/${codeId}/revoke`, { reason });
}

/** Re-sends an already-issued code's email, without creating a new code. */
export function resendAccessCode(codeId: number): Promise<AccessCode> {
  return post(`/api/admin/access-codes/${codeId}/resend`, {});
}

export function extendUserAccess(userId: number, additionalDays: number): Promise<AdminUser> {
  return post(`/api/admin/users/${userId}/access/extend`, { additional_days: additionalDays });
}

export function revokeUserAccess(userId: number, reason?: string): Promise<AdminUser> {
  return post(`/api/admin/users/${userId}/access/revoke`, { reason });
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

/** Every market the outcome registry offers for this one match. */
export function fetchMatchOutcomes(matchId: number): Promise<OutcomesResponse> {
  return get(`/api/matches/${matchId}/outcomes`);
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

// --- Super Admin settings --------------------------------------------------

export function fetchSettings(): Promise<SettingsPayload> {
  return get("/api/admin/settings");
}

export function saveSettings(payload: {
  values?: Record<string, string | number | boolean>;
  reset?: string[];
}): Promise<SettingsPayload> {
  return patch("/api/admin/settings", payload);
}

export function sendTestEmail(to?: string): Promise<TestEmailResult> {
  return post("/api/admin/settings/test-email", { to });
}

export function fetchSystemStatus(): Promise<SystemStatus> {
  return get("/api/admin/system-status");
}

export function fetchBranding(): Promise<Branding> {
  return get("/api/public/branding");
}

/** No auth needed -- shown to every visitor, logged in or not. */
export function fetchNotice(): Promise<SiteNotice> {
  return get("/api/public/notice");
}

export function fetchOutcomes(params: {
  market?: string;
  league?: string;
  days_ahead?: number;
  min_probability?: number;
  confidence?: string;
}): Promise<OutcomesResponse> {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== "" && v !== null) query.set(k, String(v));
  });
  return get(`/api/predictions/outcomes?${query.toString()}`);
}
