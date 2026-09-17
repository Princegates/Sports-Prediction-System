import { createContext, useContext, useEffect, useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { SearchCommand } from "./SearchCommand";
import { useAuth } from "../lib/AuthContext";

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

const NAV_ITEMS = [
  { to: "/", label: "Dashboard", icon: "◆" },
  { to: "/predictions", label: "Predictions", icon: "▤" },
  { to: "/live", label: "Live", icon: "●" },
];

type Theme = "dark" | "light";

function readStoredTheme(): Theme {
  try {
    return (localStorage.getItem("theme") as Theme) || "dark";
  } catch {
    return "dark";
  }
}

function useTheme() {
  const [theme, setTheme] = useState<Theme>(readStoredTheme);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    try {
      localStorage.setItem("theme", theme);
    } catch {
      // private-browsing / storage-disabled -- theme just won't persist
    }
  }, [theme]);

  return [theme, setTheme] as const;
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
  const [theme, setTheme] = useTheme();
  const { user } = useAuth();
  const navItems = user?.role === "superadmin" ? [...NAV_ITEMS, { to: "/admin", label: "Admin", icon: "⚙" }] : NAV_ITEMS;

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
          <a href="/" className="brand">
            <span className="brand-mark">AI</span>
            <span className="brand-text">
              <strong>Match Intelligence</strong>
              <span>Football AI</span>
            </span>
          </a>

          <nav className="nav-group">
            <div className="nav-label">Intelligence</div>
            {navItems.map((item) => (
              <NavLink key={item.to} to={item.to} end={item.to === "/"} className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}>
                <span className="nav-icon">{item.icon}</span>
                {item.label}
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
            <ThemeToggle theme={theme} onToggle={() => setTheme(theme === "dark" ? "light" : "dark")} />
          </header>

          <header className="mobile-topbar">
            <a href="/" className="brand">
              <span className="brand-mark">AI</span>
              <span className="brand-text">
                <strong>Match Intelligence</strong>
              </span>
            </a>
            <div style={{ display: "flex", gap: 8 }}>
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
            <NavLink key={item.to} to={item.to} end={item.to === "/"} className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}>
              <span className="nav-icon">{item.icon}</span>
              {item.label}
            </NavLink>
          ))}
        </nav>

        <SearchCommand open={searchOpen} onClose={() => setSearchOpen(false)} leagues={LEAGUES} />
      </div>
    </LeagueContext.Provider>
  );
}

export { LEAGUES };
