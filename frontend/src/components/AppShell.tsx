import { createContext, useContext, useEffect, useState } from "react";
import { Link, NavLink, Outlet, useNavigate } from "react-router-dom";
import { SearchCommand } from "./SearchCommand";
import { ChatDock } from "./ChatDock";
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
  "Turkish Süper Lig",
  "UEFA Champions League",
];

interface LeagueContextValue {
  // "" means "all leagues" -- every league-scoped page treats a falsy
  // league as no filter (see fetchMatches/fetchMostLikely in api.ts), so
  // this one sentinel works everywhere the topbar select is consumed.
  league: string;
  setLeague: (l: string) => void;
}

const LeagueContext = createContext<LeagueContextValue>({ league: LEAGUES[0], setLeague: () => {} });
export const useLeague = () => useContext(LeagueContext);

/** Display text for a `useLeague()` value, including the "all leagues" sentinel. */
export function leagueLabel(league: string): string {
  return league || "all leagues";
}

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
  { to: "/app/betcodes", label: "AI Generation", icon: "▦" },
  { to: "/app/live", label: "Live", icon: "●" },
];

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
  const { user, accessStatus } = useAuth();
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
              <strong>Socca Intelligence</strong>
              <span>Football prediction AI</span>
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
              <option value="">All leagues</option>
              {LEAGUES.map((l) => (
                <option key={l} value={l}>
                  {l}
                </option>
              ))}
            </select>
          </header>

          <header className="mobile-topbar">
            <Link to="/app" className="brand">
              <span className="brand-mark">AI</span>
              <span className="brand-text">
                <strong>Socca Intelligence</strong>
              </span>
            </Link>
            <div style={{ display: "flex", gap: 8 }}>
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
