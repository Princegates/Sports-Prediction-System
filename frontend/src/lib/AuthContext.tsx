import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  AuthExpiredError,
  clearStoredToken,
  fetchAccessStatus,
  fetchMe,
  getStoredToken,
  login as apiLogin,
  onAuthLogout,
  storeToken,
  updatePreferences as apiUpdatePreferences,
  updateProfile as apiUpdateProfile,
} from "../api";
import type { AccessStatus, User } from "../types";

// The backend's free-tier host sleeps after ~15 minutes idle and can take
// 50+ seconds to wake (see render.yaml) -- sometimes refusing connections
// outright for the first few seconds. Retried with backoff spanning that
// window rather than treated as "logged out" on the first failure; total
// worst case here is ~60s before giving up, which only happens right after
// the backend has been idle.
const SESSION_LOAD_RETRY_DELAYS_MS = [0, 2000, 4000, 8000, 16000, 30000];

interface AuthContextValue {
  user: User | null;
  loading: boolean;
  // Whether the account has a live access grant -- separate from `user`
  // being set, since login no longer implies feature access. `null` while
  // still loading or for a superadmin (who never needs a grant).
  accessStatus: AccessStatus | null;
  accessLoading: boolean;
  refreshAccessStatus: () => Promise<void>;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
  updateProfile: (name: string) => Promise<void>;
  updatePreferences: (payload: { notify_weekly_picks?: boolean }) => Promise<void>;
}

const AuthContext = createContext<AuthContextValue>({
  user: null,
  loading: true,
  accessStatus: null,
  accessLoading: true,
  refreshAccessStatus: async () => {},
  login: async () => {},
  logout: () => {},
  updateProfile: async () => {},
  updatePreferences: async () => {},
});

export function useAuth() {
  return useContext(AuthContext);
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [accessStatus, setAccessStatus] = useState<AccessStatus | null>(null);
  const [accessLoading, setAccessLoading] = useState(true);

  const logout = useCallback(() => {
    clearStoredToken();
    setUser(null);
    setAccessStatus(null);
  }, []);

  const refreshAccessStatus = useCallback(async () => {
    setAccessLoading(true);
    try {
      setAccessStatus(await fetchAccessStatus());
    } catch {
      // Leave whatever access status we last knew in place. Overwriting it
      // with null here used to mean a single flaky request (the backend
      // waking up, a dropped connection) could flip someone who *does* have
      // access into seeing "no active access yet" -- exactly the kind of
      // thing that looks like the app randomly breaking.
    } finally {
      setAccessLoading(false);
    }
  }, []);

  useEffect(() => {
    const token = getStoredToken();
    if (!token) {
      setLoading(false);
      setAccessLoading(false);
      return;
    }

    let cancelled = false;

    async function loadSession() {
      for (let attempt = 0; attempt < SESSION_LOAD_RETRY_DELAYS_MS.length; attempt++) {
        if (attempt > 0) {
          await new Promise((resolve) => setTimeout(resolve, SESSION_LOAD_RETRY_DELAYS_MS[attempt]));
          if (cancelled) return;
        }
        try {
          const u = await fetchMe();
          if (cancelled) return;
          setUser(u);
          // A superadmin never needs a grant, so there's nothing to fetch --
          // this also keeps the admin panel from ever being gated on it.
          if (u.role === "superadmin") {
            setAccessStatus({ has_access: true, status: "active", activated_at: null, expires_at: null });
            setAccessLoading(false);
          } else {
            await refreshAccessStatus();
          }
          setLoading(false);
          return;
        } catch (err) {
          if (err instanceof AuthExpiredError) {
            // The token itself was rejected (already cleared by the 401
            // handler) -- this one really is "logged out," no retry helps.
            setAccessLoading(false);
            setLoading(false);
            return;
          }
          // Anything else is a network/server-level failure -- try again;
          // the token is presumably still good.
        }
      }
      // Retries exhausted without ever reaching the server. The stored
      // token is left alone (it may well still be valid) -- just stop
      // waiting so the UI can show something instead of spinning forever.
      setAccessLoading(false);
      setLoading(false);
    }

    loadSession();
    return () => {
      cancelled = true;
    };
  }, [refreshAccessStatus]);

  useEffect(() => onAuthLogout(() => setUser(null)), []);

  const login = useCallback(
    async (email: string, password: string) => {
      const result = await apiLogin(email, password);
      storeToken(result.access_token);
      setUser(result.user);
      if (result.user.role === "superadmin") {
        setAccessStatus({ has_access: true, status: "active", activated_at: null, expires_at: null });
        setAccessLoading(false);
      } else {
        await refreshAccessStatus();
      }
    },
    [refreshAccessStatus],
  );

  const updateProfile = useCallback(async (name: string) => {
    const updated = await apiUpdateProfile(name);
    setUser(updated);
  }, []);

  const updatePreferences = useCallback(async (payload: { notify_weekly_picks?: boolean }) => {
    const updated = await apiUpdatePreferences(payload);
    setUser(updated);
  }, []);

  const value = useMemo(
    () => ({
      user,
      loading,
      accessStatus,
      accessLoading,
      refreshAccessStatus,
      login,
      logout,
      updateProfile,
      updatePreferences,
    }),
    [user, loading, accessStatus, accessLoading, refreshAccessStatus, login, logout, updateProfile, updatePreferences],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
