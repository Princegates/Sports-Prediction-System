import { createContext, useContext, useEffect, useRef, useState } from "react";
import { Link, NavLink, Outlet, useNavigate } from "react-router-dom";
import { SearchCommand } from "./SearchCommand";
import { AccentPicker } from "./AccentPicker";
import { ChatDock } from "./ChatDock";
import { useAuth } from "../lib/AuthContext";
import { readStoredAccent, storeAccent } from "../lib/accentProfiles";

const LEAGUES = [
  "English Premier League",
  "English Championship",
  "Spanish La Liga",
  "German Bundesliga",
  "Italian Serie A",
  "French Ligue 1",
  "Dutch Eredivisie",
  "Portuguese Primeira Liga",
  "UEFA Champions League",
];

interface LeagueContextValue {
  league: string;
  setLeague: (l: string) => void;
}

const LeagueContext = createContext<LeagueContextValue>({ league: LEAGUES[0], setLeague: () => {} });
export const useLeague = () => useContext(LeagueContext);

interface NavItem {
  to: string;
  label: string;
  icon: string;
  badge?: boolean;
}

const NAV_ITEMS: NavItem[] = [
  { to: "/app", label: "Dashboard", icon: "◆" },
  { to: "/app/predictions", label: "Predictions", icon: "▤" },
  { to: "/app/markets", label: "Markets", icon: "◈" },
  { to: "/app/live", label: "Live", icon: "●" },
];

type Theme = "dark" | "light";

function readStoredTheme(): Theme {
  try {
    return (localStorage.getItem("theme") as Theme) || "dark";
  } catch {
    return "dark";
  }
}

/** Every user has their own saved theme/accent -- once they actively change
 * it here, it's pushed to their account (not just this browser) via
 * onPersist, skipping the very first effect run so mounting doesn't
 * immediately re-save the value it just read. */
function useTheme(onPersist?: (theme: Theme) => void) {
  const [theme, setTheme] = useState<Theme>(readStoredTheme);
  const firstRun = useRef(true);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    try {
      localStorage.setItem("theme", theme);
    } catch {
      // private-browsing / storage-disabled -- theme just won't persist
    }
    if (firstRun.current) {
      firstRun.current = false;
      return;
    }
    onPersist?.(theme);
  }, [theme]);

  return [theme, setTheme] as const;
}

function useAccent(onPersist?: (accent: string) => void) {
  const [accent, setAccent] = useState<string>(readStoredAccent);
  const firstRun = useRef(true);

  useEffect(() => {
    document.documentElement.setAttribute("data-accent", accent);
    storeAccent(accent);
    if (firstRun.current) {
      firstRun.current = false;
      return;
    }
    onPersist?.(accent);
  }, [accent]);

  return [accent, setAccent] as const;
}

function ThemeToggle({ theme, onToggle }: { theme: Theme; onToggle: () => void }) {
  return (
    <button className="btn ghost" onClick={onToggle} aria-label={theme === "dark" ? "Switch to day theme" : "Switch to night theme"} title={theme === "dark" ? "Day" : "Night"}>
      {theme === "dark" ? "☀" : "☾"}
    </button>
  );
}

function UserMenu() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  if (!user) return null;

  function handleLogout() {
    logout();
    navigate("/login", { replace: true });
  }

  return (
    <div className="user-menu">
      <span className="user-menu-name" title={user.email}>
        {user.name}
        {user.role === "superadmin" && <span className="status-tag active" style={{ marginLeft: 6 }}>admin</span>}
      </span>
      <button className="btn ghost" onClick={handleLogout}>
        Sign out
      </button>
    </div>
  );
}

export function AppShell() {
  const [league, setLeague] = useState(LEAGUES[0]);
  const [searchOpen, setSearchOpen] = useState(false);
  const { user, setPreferences, accessStatus } = useAuth();
  const [theme, setTheme] = useTheme((t) => setPreferences({ theme: t }).catch(() => {}));
  const [accent, setAccent] = useAccent((a) => setPreferences({ accent_profile: a }).catch(() => {}));
  const needsAccess = user?.role !== "superadmin" && !accessStatus?.has_access;
  const navItems =
    user?.role === "superadmin"
      ? [
          ...NAV_ITEMS,
          { to: "/app/profile", label: "Profile", icon: "◍" },
          { to: "/app/admin", label: "Admin", icon: "⚙" },
          { to: "/app/admin/settings", label: "Settings", icon: "⚒" },
        ]
      : [
          ...NAV_ITEMS,
          { to: "/app/access", label: "Access", icon: "⚿", badge: needsAccess },
          { to: "/app/profile", label: "Profile", icon: "◍" },
        ];

  useEffect(() => {
    function handler(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setSearchOpen(true);
      }
    }
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  return (
    <LeagueContext.Provider value={{ league, setLeague }}>
      <div className="app-shell">
        <aside className="app-sidebar">
          <Link to="/app" className="brand">
            <span className="brand-mark">AI</span>
            <span className="brand-text">
              <strong>Match Intelligence</strong>
              <span>Football AI</span>
            </span>
          </Link>

          <nav className="nav-group">
            <div className="nav-label">Intelligence</div>
            {navItems.map((item) => (
              <NavLink key={item.to} to={item.to} end={item.to === "/app"} className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}>
                <span className="nav-icon">{item.icon}</span>
                {item.label}
                {item.badge && <span className="queue-badge">!</span>}
              </NavLink>
            ))}
          </nav>

          <nav className="nav-group">
            <div className="nav-label">Discover</div>
            <button className="nav-link" style={{ width: "100%", border: "none", background: "none", cursor: "pointer", textAlign: "left" }} onClick={() => setSearchOpen(true)}>
              <span className="nav-icon">⌕</span>
              Search teams
            </button>
          </nav>

          <UserMenu />

          <div className="sidebar-footer">
            Predictions are model probabilities based on historical validation, never a guarantee of outcome.
          </div>
        </aside>

        <div className="app-main">
          <header className="app-topbar">
            <div className="topbar-search" onClick={() => setSearchOpen(true)}>
              <span aria-hidden>⌕</span>
              <span>Search teams...</span>
              <kbd>&#8984;K</kbd>
            </div>
            <select className="topbar-league" value={league} onChange={(e) => setLeague(e.target.value)} aria-label="Select league">
              {LEAGUES.map((l) => (
                <option key={l} value={l}>
                  {l}
                </option>
              ))}
            </select>
            <AccentPicker accent={accent} onChange={setAccent} />
            <ThemeToggle theme={theme} onToggle={() => setTheme(theme === "dark" ? "light" : "dark")} />
          </header>

          <header className="mobile-topbar">
            <Link to="/app" className="brand">
              <span className="brand-mark">AI</span>
              <span className="brand-text">
                <strong>Match Intelligence</strong>
              </span>
            </Link>
            <div style={{ display: "flex", gap: 8 }}>
              <AccentPicker accent={accent} onChange={setAccent} />
              <ThemeToggle theme={theme} onToggle={() => setTheme(theme === "dark" ? "light" : "dark")} />
              <button className="btn ghost" onClick={() => setSearchOpen(true)} aria-label="Search">
                ⌕
              </button>
            </div>
          </header>

          <main className="app-content">
            <Outlet />
          </main>
        </div>

        <nav className="mobile-bottom-nav">
          {navItems.map((item) => (
            <NavLink key={item.to} to={item.to} end={item.to === "/app"} className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}>
              <span className="nav-icon">{item.icon}</span>
              {item.label}
              {item.badge && <span className="queue-badge">!</span>}
            </NavLink>
          ))}
        </nav>

        <SearchCommand open={searchOpen} onClose={() => setSearchOpen(false)} leagues={LEAGUES} />

        {/* Mounted at shell level rather than per page, so the conversation
            survives navigation between matches -- which is the whole point of
            being able to ask a follow-up about the fixture you just opened. */}
        <ChatDock />
      </div>
    </LeagueContext.Provider>
  );
}

export { LEAGUES };
