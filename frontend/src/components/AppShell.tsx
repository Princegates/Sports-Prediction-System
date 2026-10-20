import { createContext, useContext, useEffect, useRef, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { Search, UserAvatar, SettingsIcon, Locked, UserFollow } from "./icons";
import { SearchCommand } from "./SearchCommand";
import { AccountBanners } from "./AccountBanners";
import { ChatDock } from "./ChatDock";
import { MobileBottomNav } from "./MobileBottomNav";
import { ThemeToggle } from "./ThemeToggle";
import { useAuth } from "../lib/AuthContext";

const LEAGUES = [
  "English Premier League",
  "Spanish La Liga",
  "German Bundesliga",
  "Italian Serie A",
  "French Ligue 1",
  "Dutch Eredivisie",
  "Portuguese Primeira Liga",
  "Turkish Süper Lig",
  "UEFA Champions League",
  "UEFA Europa League",
  "English Championship",
  "Albanian Superliga",
  "Andorran Primera Divisió",
  "Armenian Premier League",
  "Austrian Bundesliga",
  "Azerbaijan Premyer Liqa",
  "Belarusian Premier League",
  "Belgian Pro League",
  "Bosnian Premijer Liga",
  "Bulgarian First League",
  "Croatian HNL",
  "Cypriot First Division",
  "Czech First League",
  "Danish Superliga",
  "Estonian Meistriliiga",
  "Faroese Meistaradeildin",
  "Finnish Veikkausliiga",
  "Georgian Erovnuli Liga",
  "Gibraltar Premier Division",
  "Greek Super League",
  "Hungarian NB I",
  "Icelandic Úrvalsdeild",
  "League of Ireland Premier Division",
  "Israeli Ligat Ha'al",
  "Kazakh Premier League",
  "Kosovan Superliga",
  "Latvian Virslīga",
  "Lithuanian A Lyga",
  "Luxembourg National Division",
  "Macedonian First League",
  "Maltese Premier League",
  "Moldovan Super Liga",
  "Montenegrin First League",
  "NIFL Premiership",
  "Norwegian Eliteserien",
  "Polish Ekstraklasa",
  "Romanian Liga I",
  "San Marino Campionato",
  "Scottish Premiership",
  "Serbian SuperLiga",
  "Slovak Super Liga",
  "Slovenian PrvaLiga",
  "Swedish Allsvenskan",
  "Swiss Super League",
  "Ukrainian Premier League",
  "Welsh Premier League",
];

// The 8 biggest domestic leagues this deployment tracks plus the
// Champions League -- the same leading slice LEAGUES above already orders
// first, before it drops into continental competitions and the smaller
// leagues beyond them. Markets and Predictions default to just these on
// first load (and whenever nothing else is explicitly picked) instead of
// every league this deployment tracks -- loading matches and outcomes
// across 54 leagues by default made the initial load slow for no benefit
// most visits never needed; "All leagues" is one click away in the
// picker's own presets.
const TOP_LEAGUES = LEAGUES.slice(0, 9);

const LEAGUE_STORAGE_KEY = "app_league";

/** The topbar league selector survives a reload -- otherwise every visit
 * (or filter change elsewhere that triggers one) silently drops back to
 * "All leagues" and the user re-picks the same one again. Falls back to ""
 * ("all leagues") for a value that's missing, unreadable (private browsing),
 * or no longer a real league (list changed since it was saved). */
function readStoredLeague(): string {
  try {
    const stored = localStorage.getItem(LEAGUE_STORAGE_KEY);
    return stored && LEAGUES.includes(stored) ? stored : "";
  } catch {
    return "";
  }
}

interface LeagueContextValue {
  // "" means "all leagues" -- every league-scoped page treats a falsy
  // league as no filter (see fetchMatches/fetchMostLikely in api.ts), so
  // this one sentinel works everywhere the topbar select is consumed.
  league: string;
  setLeague: (l: string) => void;
}

const LeagueContext = createContext<LeagueContextValue>({ league: "", setLeague: () => {} });
export const useLeague = () => useContext(LeagueContext);

/** Display text for a `useLeague()` value, including the "all leagues" sentinel. */
export function leagueLabel(league: string): string {
  return league || "all leagues";
}

interface NavItem {
  to: string;
  label: string;
  badge?: boolean;
}

const NAV_ITEMS: NavItem[] = [
  { to: "/app", label: "Dashboard" },
  { to: "/app/ai-picks", label: "AI Picks" },
  { to: "/app/predictions", label: "Predictions" },
  { to: "/app/markets", label: "Markets" },
  { to: "/app/betcodes", label: "AI Generation" },
  { to: "/app/live", label: "Live" },
];

/** Segmented pill tab bar (top) + an avatar dropdown for every
 * account-level action (profile, access, superadmin tools, sign out) --
 * the two-tier nav described in the redesign scope doc, with no Carbon
 * dependency anywhere. */
export function AppShell() {
  // "" ("All leagues") on purpose, not LEAGUES[0] -- Dashboard and Live
  // degrade to an unfiltered (cross-league) query just fine when league is
  // falsy, and AI Generation previously defaulted to "any league" before it
  // was wired to this shared selector, so defaulting here to one specific
  // league would have silently narrowed what it searches on first load.
  const [league, setLeague] = useState(readStoredLeague);
  const [searchOpen, setSearchOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const { user, accessStatus, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const menuRef = useRef<HTMLDivElement>(null);
  // Trailing-slash-tolerant: react-router normally gives an exact "/app"
  // for the Dashboard route, but this is cheap insurance against a stray
  // "/app/" never showing the control anywhere.
  const isDashboard = location.pathname === "/app" || location.pathname === "/app/";
  const isSuperadmin = user?.role === "superadmin";
  const needsAccess = !isSuperadmin && !accessStatus?.has_access;

  useEffect(() => {
    try {
      if (league) localStorage.setItem(LEAGUE_STORAGE_KEY, league);
      else localStorage.removeItem(LEAGUE_STORAGE_KEY);
    } catch {
      // private-browsing / storage-disabled -- just won't persist across reloads
    }
  }, [league]);

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

  // Closes the account menu on an outside click -- the dropdown has no
  // backdrop of its own (it sits inline in the topbar), so this is what
  // makes it dismissable the way a native menu would be.
  useEffect(() => {
    if (!menuOpen) return;
    function onDocClick(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [menuOpen]);

  // Closes the menu on navigation, so it never sits open over the next page.
  useEffect(() => {
    setMenuOpen(false);
  }, [location.pathname]);

  function handleLogout() {
    setMenuOpen(false);
    logout();
    navigate("/login", { replace: true });
  }

  return (
    <LeagueContext.Provider value={{ league, setLeague }}>
      <header className="topbar" aria-label="Socca Intelligence">
        <NavLink to="/app" className="topbar-brand">
          <span className="topbar-brand-mark">SI</span>
          <span className="topbar-brand-full">Socca Intelligence</span>
          <span className="topbar-brand-short">Socca</span>
        </NavLink>

        <nav className="topbar-tabs" aria-label="Primary">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/app"}
              className={({ isActive }) => `topbar-tab${isActive ? " active" : ""}`}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>

        <span className="topbar-spacer" />

        {isDashboard && (
          <select
            aria-label="League"
            className="topbar-league-select"
            value={league}
            onChange={(e) => setLeague(e.target.value)}
          >
            <option value="">All leagues</option>
            {LEAGUES.map((l) => (
              <option key={l} value={l}>
                {l}
              </option>
            ))}
          </select>
        )}

        <div className="topbar-actions">
          <ThemeToggle />

          <button type="button" className="topbar-icon-btn" aria-label="Search teams" onClick={() => setSearchOpen(true)}>
            <Search size={18} />
          </button>

          <div className="account-menu" ref={menuRef}>
            <button
              type="button"
              className="topbar-avatar"
              aria-label="Account menu"
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen((v) => !v)}
            >
              <UserAvatar size={16} />
              {needsAccess && <span className="queue-badge">!</span>}
            </button>
            {menuOpen && (
              <div className="account-menu-panel" role="menu">
                <button type="button" className="account-menu-item" onClick={() => navigate("/app/profile")}>
                  <UserAvatar size={16} /> Profile
                </button>
                {needsAccess && (
                  <button type="button" className="account-menu-item" onClick={() => navigate("/app/access")}>
                    <Locked size={16} /> Redeem access
                  </button>
                )}
                {isSuperadmin && (
                  <>
                    <button type="button" className="account-menu-item" onClick={() => navigate("/app/admin")}>
                      <SettingsIcon size={16} /> Admin
                    </button>
                    <button type="button" className="account-menu-item" onClick={() => navigate("/app/admin/activity-log")}>
                      <SettingsIcon size={16} /> Activity Log
                    </button>
                    <button type="button" className="account-menu-item" onClick={() => navigate("/app/admin/settings")}>
                      <SettingsIcon size={16} /> Settings
                    </button>
                  </>
                )}
                <button type="button" className="account-menu-item" onClick={handleLogout}>
                  <UserFollow size={16} /> Sign out
                </button>
              </div>
            )}
          </div>
        </div>
      </header>

      <main id="main-content" className="app-content">
        <AccountBanners />
        <Outlet />
      </main>

      <SearchCommand open={searchOpen} onClose={() => setSearchOpen(false)} />

      {/* Mounted at shell level rather than per page, so the conversation
          survives navigation between matches -- which is the whole point of
          being able to ask a follow-up about the fixture you just opened. */}
      <ChatDock />

      <MobileBottomNav />
    </LeagueContext.Provider>
  );
}

export { LEAGUES, TOP_LEAGUES };
