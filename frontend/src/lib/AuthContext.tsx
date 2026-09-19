import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  clearStoredToken,
  fetchAccessStatus,
  fetchMe,
  getStoredToken,
  login as apiLogin,
  onAuthLogout,
  storeToken,
  updateProfile as apiUpdateProfile,
} from "../api";
import type { AccessStatus, User } from "../types";

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
      setAccessStatus(null);
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
    fetchMe()
      .then((u) => {
        setUser(u);
        // A superadmin never needs a grant, so there's nothing to fetch --
        // this also keeps the admin panel from ever being gated on it.
        if (u.role === "superadmin") {
          setAccessStatus({ has_access: true, status: "active", activated_at: null, expires_at: null });
          setAccessLoading(false);
          return;
        }
        refreshAccessStatus();
      })
      .catch(() => clearStoredToken())
      .finally(() => setLoading(false));
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
    }),
    [user, loading, accessStatus, accessLoading, refreshAccessStatus, login, logout, updateProfile],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
