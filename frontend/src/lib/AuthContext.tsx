import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { clearStoredToken, fetchMe, getStoredToken, login as apiLogin, onAuthLogout, storeToken, updatePreferences } from "../api";
import { storeAccent } from "./accentProfiles";
import type { User } from "../types";

interface AuthContextValue {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
  setPreferences: (prefs: { theme?: string; accent_profile?: string }) => Promise<void>;
}

const AuthContext = createContext<AuthContextValue>({
  user: null,
  loading: true,
  login: async () => {},
  logout: () => {},
  setPreferences: async () => {},
});

/** A signed-in user's saved theme/accent (their own account, not the
 * browser) wins over whatever this device had stored locally. */
function applyStoredPreferences(user: User) {
  if (user.theme === "light" || user.theme === "dark") {
    document.documentElement.setAttribute("data-theme", user.theme);
    try {
      localStorage.setItem("theme", user.theme);
    } catch {
      // private-browsing / storage-disabled -- attribute is still applied for this session
    }
  }
  if (user.accent_profile) {
    document.documentElement.setAttribute("data-accent", user.accent_profile);
    storeAccent(user.accent_profile);
  }
}

export function useAuth() {
  return useContext(AuthContext);
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const logout = useCallback(() => {
    clearStoredToken();
    setUser(null);
  }, []);

  useEffect(() => {
    const token = getStoredToken();
    if (!token) {
      setLoading(false);
      return;
    }
    fetchMe()
      .then((u) => {
        applyStoredPreferences(u);
        setUser(u);
      })
      .catch(() => clearStoredToken())
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => onAuthLogout(() => setUser(null)), []);

  const login = useCallback(async (email: string, password: string) => {
    const result = await apiLogin(email, password);
    storeToken(result.access_token);
    applyStoredPreferences(result.user);
    setUser(result.user);
  }, []);

  const setPreferences = useCallback(async (prefs: { theme?: string; accent_profile?: string }) => {
    const updated = await updatePreferences(prefs);
    setUser(updated);
  }, []);

  const value = useMemo(() => ({ user, loading, login, logout, setPreferences }), [user, loading, login, logout, setPreferences]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
