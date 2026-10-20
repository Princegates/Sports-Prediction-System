import type {
  AdminPick,
  AdminPickTrackRecord,
  BetCodePick,
  BettingSite,
  MyBookingSlip,
  PicksBooking,
  SuggestCriteria,
  SuggestRandomCriteria,
  SuggestedPicks,
  OutcomesResponse,
  Branding,
  SettingsPayload,
  SystemStatus,
  BettingSiteCheck,
  TestEmailResult,
  AccessCode,
  AccessGrant,
  AccessStatus,
  AccountStatus,
  AdminOverview,
  AdminUser,
  AuditLogPage,
  ChatAnswer,
  ChatMessage,
  FeaturedPick,
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
  PublicTeaserPick,
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

/** Thrown only for a real 401 -- the token itself was rejected, and has
 * already been cleared. Distinct from a plain Error (network failure, a 5xx,
 * the backend still waking up) so a caller like AuthContext can tell "you
 * are genuinely logged out" apart from "couldn't reach it just now" and
 * stop treating the second as the first -- see the fix in AuthContext for
 * why that distinction matters on a host that sleeps when idle. */
export class AuthExpiredError extends Error {}

// Plain `fetch` has no default timeout -- on a flaky mobile connection a
// stalled TCP read can leave a request's promise never settling at all, not
// just slow. A caller like AIPicks (four sections, each waiting on its own
// fetch before the page stops showing its "Loading..." state) then hangs
// forever instead of eventually erroring, since .then/.catch/.finally never
// fire. 20s comfortably covers a sleeping free-tier backend's wake-up (a
// few seconds, per the catch block below) while still giving up well short
// of "feels broken."
const REQUEST_TIMEOUT_MS = 20_000;

async function request<T>(path: string, options: RequestInit = {}, timeoutMs: number = REQUEST_TIMEOUT_MS): Promise<T> {
  const token = getStoredToken();
  const headers: Record<string, string> = { ...(options.headers as Record<string, string>) };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (options.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";

  const timeoutController = new AbortController();
  const timeoutId = setTimeout(() => timeoutController.abort(), timeoutMs);

  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, { ...options, headers, signal: timeoutController.signal });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new Error(`Request timed out -- ${path}`);
    }
    // A network-level failure (DNS, connection refused, CORS preflight
    // dropped) -- common for a few seconds while a sleeping free-tier
    // backend wakes up. Never the same thing as a rejected token.
    throw new Error(`Couldn't reach the server -- ${path}`);
  } finally {
    clearTimeout(timeoutId);
  }

  if (response.status === 401) {
    clearStoredToken();
    window.dispatchEvent(new Event(AUTH_LOGOUT_EVENT));
    throw new AuthExpiredError("Session expired -- please log in again.");
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

function post<T>(path: string, body: unknown, timeoutMs?: number): Promise<T> {
  return request(path, { method: "POST", body: JSON.stringify(body) }, timeoutMs);
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

export function registerAccount(payload: { email: string; name: string; password: string; date_of_birth: string; referral_code?: string }) {
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

/** One fixture's public preview -- feeds its own page (lib/slug.ts builds
 * the URL). Throws (via `get`'s normal error path) on a 404, same as any
 * other missing resource -- the page calling this is responsible for
 * showing a "not found" state rather than this function inventing one. */
export function fetchPublicFixture(matchId: number): Promise<PublicFixture> {
  return get(`/api/public/fixtures/${matchId}`);
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

export function updateProfile(name: string): Promise<User> {
  return patch("/api/auth/profile", { name });
}

export function updatePreferences(payload: { notify_weekly_picks?: boolean }): Promise<User> {
  return patch("/api/auth/preferences", payload);
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

export interface AuditLogFilters {
  limit?: number;
  offset?: number;
  userId?: number;
  action?: string;
  since?: string;
  until?: string;
}

export function fetchAuditLog(filters: AuditLogFilters = {}): Promise<AuditLogPage> {
  const params = new URLSearchParams();
  if (filters.limit != null) params.set("limit", String(filters.limit));
  if (filters.offset != null) params.set("offset", String(filters.offset));
  if (filters.userId != null) params.set("user_id", String(filters.userId));
  if (filters.action) params.set("action", filters.action);
  if (filters.since) params.set("since", filters.since);
  if (filters.until) params.set("until", filters.until);
  return get(`/api/admin/audit-log?${params.toString()}`);
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

/** Outcomes a Super Admin has chosen to highlight -- whether a free-tier
 * account sees this at all is the operator's call (guda_picks_free_tier_visible),
 * but every probability shown is the match's current one, never a snapshot. */
export function fetchGudaPicks(): Promise<FeaturedPick[]> {
  return get("/api/predictions/guda-picks");
}

/** Superadmin only -- promotes one real outcome from a match's own Markets
 * tab. Rejected if the market/selection pair isn't a real current outcome
 * of that match, or is already featured. */
export function featurePick(payload: { match_id: number; market: string; selection: string; note?: string }): Promise<FeaturedPick> {
  return post("/api/admin/featured-picks", payload);
}

/** Edits a Guda Pick's note in place -- the match/market/selection it
 * references can't change here (see the backend route's own docstring). */
export function updateFeaturedPickNote(pickId: number, note: string): Promise<FeaturedPick> {
  return patch(`/api/admin/featured-picks/${pickId}`, { note });
}

export function fetchFeaturedPicksAdmin(): Promise<FeaturedPick[]> {
  return get("/api/admin/featured-picks");
}

export function unfeaturePick(pickId: number): Promise<void> {
  return del(`/api/admin/featured-picks/${pickId}`);
}

/** Manual settlement override -- the fallback for a market
 * app.outcomes.grading doesn't auto-grade ("unresolved"), or to correct a
 * mistake. "pending" un-freezes the pick for re-settling from scratch. */
export function setFeaturedPickResult(
  pickId: number,
  result: "pending" | "won" | "lost" | "unresolved",
): Promise<FeaturedPick> {
  return patch(`/api/admin/featured-picks/${pickId}/result`, { result });
}

/** An admin's choice of which settled picks show in the public track
 * record (fetchAdminPickTrackRecord) -- rejected while the pick is still
 * "pending"; unfeaturing is always allowed. */
export function setFeaturedPickFeaturedPublic(pickId: number, featuredPublic: boolean): Promise<FeaturedPick> {
  return patch(`/api/admin/featured-picks/${pickId}/featured-public`, { featured_public: featuredPublic });
}

/** Whole multi-leg slips a Super Admin has chosen to highlight -- combined
 * odds, combined probability and risk tier are all recomputed live, same
 * "never a snapshot" reasoning as fetchGudaPicks. */
export function fetchAdminPicks(): Promise<AdminPick[]> {
  return get("/api/predictions/admin-picks");
}

/** Superadmin only -- promotes a whole slip. Every leg is re-resolved
 * server-side; rejected if even one no longer resolves. `priced` (default
 * true) picks how: re-priced from real bookmaker quotes (an AI Generation
 * slip), or false for model-probability-only, no bookmaker quote required --
 * for a slip built from outcomes that were never priced, like Markets' "My
 * picks" panel.
 *
 * `booking_code`/`booking_code_bookmaker` are never generated by this
 * platform -- they're whatever the admin typed after building the same
 * slip by hand on a real bookmaker's site, and the backend rejects one
 * without the other. Shown only to viewers with active premium access. */
export function createAdminPick(payload: {
  legs: { match_id: number; market: string; selection: string }[];
  label?: string;
  note?: string;
  priced?: boolean;
  booking_code?: string;
  booking_code_bookmaker?: string;
}): Promise<AdminPick> {
  return post("/api/admin/admin-picks", payload);
}

/** Replaces an already-featured slip's legs/label/note/priced/booking code
 * in place -- a full replace, same shape as createAdminPick, not a partial
 * patch. Every leg is re-resolved server-side; rejected if even one no
 * longer resolves. */
export function updateAdminPick(
  pickId: number,
  payload: {
    legs: { match_id: number; market: string; selection: string }[];
    label?: string;
    note?: string;
    priced?: boolean;
    booking_code?: string;
    booking_code_bookmaker?: string;
  },
): Promise<AdminPick> {
  return patch(`/api/admin/admin-picks/${pickId}`, payload);
}

export function fetchAdminPicksAdmin(): Promise<AdminPick[]> {
  return get("/api/admin/admin-picks");
}

export function deleteAdminPick(pickId: number): Promise<void> {
  return del(`/api/admin/admin-picks/${pickId}`);
}

/** Manual settlement override -- the fallback for a leg on a market
 * app.outcomes.grading doesn't auto-grade ("unresolved"), or to correct a
 * mistake. "pending" un-freezes the pick for re-settling from scratch. */
export function setAdminPickResult(
  pickId: number,
  result: "pending" | "won" | "lost" | "unresolved",
): Promise<AdminPick> {
  return patch(`/api/admin/admin-picks/${pickId}/result`, { result });
}

/** An admin's choice of which settled picks show in the public track
 * record (fetchAdminPickTrackRecord) -- rejected while the pick is still
 * "pending"; unfeaturing is always allowed. */
export function setAdminPickFeaturedPublic(pickId: number, featuredPublic: boolean): Promise<AdminPick> {
  return patch(`/api/admin/admin-picks/${pickId}/featured-public`, { featured_public: featuredPublic });
}

/** The platform's public, verifiable record for its curated Admin Picks --
 * aggregate win/loss counts only, no login required. See
 * AdminPickTrackRecord's own docstring for why no selections are included. */
export function fetchAdminPickTrackRecord(): Promise<AdminPickTrackRecord> {
  return get("/api/public/admin-picks-track-record");
}

/** The one showcase combo shown with real selections on the public Welcome
 * page -- see PublicTeaserPick's own docstring for why this, alone, shows
 * teams/market/selection with no login required. */
export function fetchPublicTeaserPick(): Promise<PublicTeaserPick> {
  return get("/api/public/teaser-pick");
}

/** Superadmin only -- admin's own preview of exactly what the public
 * endpoint above is currently showing. */
export function fetchPublicTeaserPickAdmin(): Promise<PublicTeaserPick> {
  return get("/api/admin/public-teaser-pick");
}

/** Superadmin only -- removes whatever teaser is currently showing, without
 * replacing it. */
export function clearPublicTeaserPick(): Promise<void> {
  return del("/api/admin/public-teaser-pick");
}

/** Superadmin only -- uses an already-generated AdminPick's own legs as the
 * new public teaser, replacing whatever was showing before. The source pick
 * itself is untouched; this creates its own row, same as randomize. Rejected
 * (400) if any leg no longer prices live right now (a finished match, a
 * pulled price). */
export function promotePickToPublicTeaser(pickId: number): Promise<PublicTeaserPick> {
  return post(`/api/admin/admin-picks/${pickId}/promote-to-teaser`, {});
}

/** Superadmin only -- publishes one of the admin's own settled booking codes
 * (from "My codes -- results history") to the public track record, as a new
 * featured_public AdminPick. Only a won or lost slip can be published (400
 * otherwise) -- an honest track record needs the losses shown too. The
 * source booking slip itself is left untouched. */
export function promoteBookingSlipToTrackRecord(slipId: number): Promise<AdminPick> {
  return post(`/api/admin/my-booking-slips/${slipId}/promote-to-track-record`, {});
}

/** A member's own generated-code history, most recent first -- every code
 * they've ever booked through bookPicks, settled against real results once
 * its matches finish. Nothing is cherry-picked: a loss stays visible
 * exactly like a win. */
export function fetchMyBookingSlips(): Promise<MyBookingSlip[]> {
  return get("/api/betcodes/mine");
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

/** Shows a masked code's full value again -- e.g. to paste it into a WhatsApp
 * message when the assigned email either isn't configured or bounced. */
export function revealAccessCode(codeId: number): Promise<AccessCode> {
  return post(`/api/admin/access-codes/${codeId}/reveal`, {});
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

/** A single day's fixtures with predictions already attached, built in one
 * batched backend request -- ``date`` (YYYY-MM-DD) picks which day, defaults
 * to today. Use this instead of fetchMatches + one fetchPrediction per
 * match: that fan-out fires one request per match in parallel, which has
 * exhausted the backend's database connection pool under real traffic. */
export function fetchTodaysPredictions(league?: string, date?: string): Promise<Prediction[]> {
  const params = new URLSearchParams();
  if (league) params.set("league", league);
  if (date) params.set("date", date);
  const query = params.toString();
  return get(`/api/predictions/today${query ? `?${query}` : ""}`);
}

/** Every genuinely-live fixture with its prediction already attached, built
 * in one batched backend request -- same reasoning as fetchTodaysPredictions,
 * for the Live page's own fetchPrediction-per-match fan-out. */
export function fetchLivePredictions(): Promise<Prediction[]> {
  return get("/api/predictions/live");
}

export function fetchMostLikely(league?: string, daysAhead = 3, limit = 10): Promise<Prediction[]> {
  const params = new URLSearchParams({ days_ahead: String(daysAhead), limit: String(limit) });
  if (league) params.set("league", league);
  return get(`/api/predictions/most-likely?${params.toString()}`);
}

/** Same endpoint as fetchMostLikely, but ``limit`` per league across every
 * league in ``leagues`` in one batched request, instead of one
 * fetchMostLikely call per league fired in parallel -- that fan-out grows
 * with the tracked-league count (54 at the time of writing) and is the same
 * connection-pool-pressure pattern Dashboard/Live had per-match. */
export function fetchMostLikelyAcrossLeagues(leagues: string[], daysAhead = 3, limit = 10): Promise<Prediction[]> {
  const params = new URLSearchParams({ days_ahead: String(daysAhead), limit: String(limit) });
  for (const l of leagues) params.append("leagues", l);
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

export function fetchMatches(
  params: { league?: string; status?: string; teamId?: number; date?: string; days_ahead?: number } = {},
): Promise<MatchSummary[]> {
  const search = new URLSearchParams();
  if (params.league) search.set("league", params.league);
  if (params.status) search.set("status", params.status);
  if (params.teamId) search.set("team_id", String(params.teamId));
  if (params.date) search.set("date", params.date);
  if (params.days_ahead !== undefined) search.set("days_ahead", String(params.days_ahead));
  const query = search.toString();
  return get(`/api/matches${query ? `?${query}` : ""}`);
}

/** Past this many leagues, a one-request-per-league fan-out (below) risks
 * opening more concurrent DB connections than the backend's pool has --
 * AI Generation's "All leagues" preset is one click away from every league
 * the platform tracks, not just a deliberate handful, so this has to hold
 * at the full catalog size. Picking this many leagues one at a time in the
 * picker is implausible; selecting all of them with one preset isn't. */
const LEAGUE_FANOUT_LIMIT = 10;

/** fetchMatches for any number of leagues -- the endpoint only ever takes
 * one. Empty `leagues` means "every league", exactly like omitting the
 * `league` param does, in a single unfiltered call rather than one call
 * per league in the system. A handful of leagues run one narrow,
 * already-scoped call per league (same query the single-league case always
 * made) and merge the results, instead of asking the backend for every
 * league and discarding most of it client-side. Past LEAGUE_FANOUT_LIMIT,
 * that per-league fan-out would open too many concurrent connections, so
 * it falls back to the same single unfiltered call as "every league" and
 * filters the result down client-side instead. */
export function fetchMatchesForLeagues(
  leagues: string[],
  params: Omit<Parameters<typeof fetchMatches>[0], "league"> = {},
): Promise<MatchSummary[]> {
  if (leagues.length === 0) return fetchMatches(params);
  if (leagues.length > LEAGUE_FANOUT_LIMIT) {
    const wanted = new Set(leagues);
    return fetchMatches(params).then((matches) => matches.filter((m) => wanted.has(m.league)));
  }
  return Promise.all(leagues.map((league) => fetchMatches({ ...params, league }))).then((results) => results.flat());
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

/** Superadmin only -- wipes every simulated event pushed to this match from
 * the Live tab's sandbox and puts the fixture back to SCHEDULED. */
export function clearMatchLiveEvents(matchId: number): Promise<MatchSummary> {
  return del(`/api/admin/matches/${matchId}/live-events`);
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

/** Makes outbound requests from the server to each betting site -- takes a few seconds. */
export function checkBettingSites(): Promise<BettingSiteCheck[]> {
  return post("/api/admin/betting-sites/check", {});
}

export function fetchBranding(): Promise<Branding> {
  return get("/api/public/branding");
}

/** No auth needed -- shown to every visitor, logged in or not. */
export function fetchNotice(): Promise<SiteNotice> {
  return get("/api/public/notice");
}

// --- Booking codes -------------------------------------------------------

/** Runs the confidence-based selection engine -- safest matches first, up
 * to a leg cap. No price, no target: the result is meant to be booked
 * directly with a betting site via bookPicks. */
export function suggestPicks(criteria: SuggestCriteria): Promise<SuggestedPicks> {
  return post("/api/betcodes/suggest", criteria);
}

/** A member's own random draw, 10-20 legs -- same real-match candidate
 * pool as suggestPicks, picked in random order instead of safest-first.
 * No price, no target: the result is meant to be booked directly with a
 * betting site via bookPicks, same as suggestPicks's own result. */
export function suggestRandomPicks(criteria: SuggestRandomCriteria = {}): Promise<SuggestedPicks> {
  return post("/api/betcodes/suggest-random", criteria);
}

/** Checks an explicit list of picks -- no search, just "resolve exactly
 * this against each match's current prediction". What "send to AI
 * Generation" (from a chat answer or a Markets-page shortlist) calls. */
export function resolvePicks(picks: BetCodePick[]): Promise<SuggestedPicks> {
  return post("/api/betcodes/resolve", { picks });
}

export function fetchBettingSites(): Promise<BettingSite[]> {
  return get("/api/betcodes/sites");
}

// Booking fans out to each chosen betting site's own API on top of
// whatever a cold free-tier backend itself costs to wake -- slower, by
// nature, than the DB-only requests REQUEST_TIMEOUT_MS was sized for, and
// the one place a visitor is most likely to hit it moments after the site
// has been idle. Six times longer gives a cold start plus a slow site
// response room to finish instead of erroring right as it was about to
// succeed, while still eventually giving up on a connection that's
// genuinely stalled rather than hanging forever.
const BOOK_PICKS_TIMEOUT_MS = 120_000;

/** Books the games and outcomes a member picked, as picked, on each chosen
 * site. No price needed -- each site prices its own slip. */
export function bookPicks(picks: BetCodePick[], sites: string[]): Promise<PicksBooking> {
  return post("/api/betcodes/picks", { picks, sites }, BOOK_PICKS_TIMEOUT_MS);
}

export function fetchOutcomes(params: {
  /** One market, or several -- sent as a repeated `market` parameter. */
  market?: string | string[];
  league?: string;
  days_ahead?: number;
  min_probability?: number;
  confidence?: string;
  limit_per_league?: number;
}): Promise<OutcomesResponse> {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (Array.isArray(v)) v.forEach((item) => query.append(k, item));
    else if (v !== undefined && v !== "" && v !== null) query.set(k, String(v));
  });
  return get(`/api/predictions/outcomes?${query.toString()}`);
}

/** fetchOutcomes for any number of leagues, same reasoning as
 * fetchMatchesForLeagues above: empty is one unfiltered call (the backend
 * already groups and caps per league for "every league"), a handful run
 * one call per league and merge the already-league-grouped responses --
 * each market's catalog entry kept once (identical across leagues), each
 * league's own group kept as the backend returned it. Past
 * LEAGUE_FANOUT_LIMIT, same fallback as fetchMatchesForLeagues: one
 * unfiltered call (which already groups every league in one response),
 * with its `leagues` entries -- and the totals that sum them, matching the
 * backend's own sum(...) exactly -- filtered down client-side. `markets`
 * is kept as-is: it's the same catalog regardless of which leagues are
 * selected, never computed per-league. */
export function fetchOutcomesForLeagues(
  leagues: string[],
  params: Omit<Parameters<typeof fetchOutcomes>[0], "league">,
): Promise<OutcomesResponse> {
  if (leagues.length === 0) return fetchOutcomes(params);
  if (leagues.length > LEAGUE_FANOUT_LIMIT) {
    const wanted = new Set(leagues);
    return fetchOutcomes(params).then((response) => {
      const kept = response.leagues.filter((l) => wanted.has(l.league));
      return {
        ...response,
        leagues: kept,
        total_outcomes: kept.reduce((sum, l) => sum + l.outcomes.length, 0),
        total_matches: kept.reduce((sum, l) => sum + l.matches, 0),
      };
    });
  }
  return Promise.all(leagues.map((league) => fetchOutcomes({ ...params, league }))).then((responses) => {
    const marketsByName = new Map(responses.flatMap((r) => r.markets).map((m) => [m.market, m]));
    return {
      markets: [...marketsByName.values()],
      leagues: responses.flatMap((r) => r.leagues),
      total_outcomes: responses.reduce((sum, r) => sum + r.total_outcomes, 0),
      total_matches: responses.reduce((sum, r) => sum + r.total_matches, 0),
      days_ahead: responses[0]?.days_ahead ?? 0,
    };
  });
}
