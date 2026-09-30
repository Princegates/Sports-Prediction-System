import { createContext, useContext, useEffect, useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import {
  Content,
  Header,
  HeaderContainer,
  HeaderGlobalAction,
  HeaderGlobalBar,
  HeaderMenuButton,
  HeaderName,
  Select,
  SelectItem,
  SideNav,
  SideNavItems,
  SideNavLink,
  SkipToContent,
} from "@carbon/react";
import { Search, UserAvatar, Settings as SettingsIcon, Locked, UserFollow } from "@carbon/icons-react";
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
  "UEFA Europa League",
];

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
  { to: "/app/predictions", label: "Predictions" },
  { to: "/app/markets", label: "Markets" },
  { to: "/app/betcodes", label: "AI Generation" },
  { to: "/app/live", label: "Live" },
];

export function AppShell() {
  // "" ("All leagues") on purpose, not LEAGUES[0] -- Dashboard and Live
  // degrade to an unfiltered (cross-league) query just fine when league is
  // falsy, and AI Generation previously defaulted to "any league" before it
  // was wired to this shared selector, so defaulting here to one specific
  // league would have silently narrowed what it searches on first load.
  const [league, setLeague] = useState(readStoredLeague);
  const [searchOpen, setSearchOpen] = useState(false);
  const { user, accessStatus, logout } = useAuth();
  const navigate = useNavigate();
  const needsAccess = user?.role !== "superadmin" && !accessStatus?.has_access;

  useEffect(() => {
    try {
      if (league) localStorage.setItem(LEAGUE_STORAGE_KEY, league);
      else localStorage.removeItem(LEAGUE_STORAGE_KEY);
    } catch {
      // private-browsing / storage-disabled -- just won't persist across reloads
    }
  }, [league]);

  const navItems =
    user?.role === "superadmin"
      ? [
          ...NAV_ITEMS,
          { to: "/app/profile", label: "Profile" },
          { to: "/app/admin", label: "Admin" },
          { to: "/app/admin/settings", label: "Settings" },
        ]
      : [
          ...NAV_ITEMS,
          { to: "/app/access", label: "Access", badge: needsAccess },
          { to: "/app/profile", label: "Profile" },
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

  function handleLogout() {
    logout();
    navigate("/login", { replace: true });
  }

  return (
    <LeagueContext.Provider value={{ league, setLeague }}>
      <HeaderContainer
        render={({ isSideNavExpanded, onClickSideNavExpand }: { isSideNavExpanded: boolean; onClickSideNavExpand: () => void }) => (
          <>
            <Header aria-label="Socca Intelligence">
              <SkipToContent />
              <HeaderMenuButton
                aria-label={isSideNavExpanded ? "Close menu" : "Open menu"}
                onClick={onClickSideNavExpand}
                isActive={isSideNavExpanded}
                isCollapsible
              />
              <HeaderName as={NavLink} to="/app" prefix="">
                Socca Intelligence
              </HeaderName>

              <HeaderGlobalBar>
                <Select
                  id="league-select"
                  labelText=""
                  hideLabel
                  size="sm"
                  value={league}
                  onChange={(e) => setLeague(e.target.value)}
                  className="app-league-select"
                >
                  <SelectItem value="" text="All leagues" />
                  {LEAGUES.map((l) => (
                    <SelectItem key={l} value={l} text={l} />
                  ))}
                </Select>
                <HeaderGlobalAction aria-label="Search teams" onClick={() => setSearchOpen(true)}>
                  <Search size={20} />
                </HeaderGlobalAction>
                {user?.role === "superadmin" && (
                  <HeaderGlobalAction aria-label="Admin" onClick={() => navigate("/app/admin")}>
                    <SettingsIcon size={20} />
                  </HeaderGlobalAction>
                )}
                {needsAccess && (
                  <HeaderGlobalAction aria-label="Redeem access" onClick={() => navigate("/app/access")}>
                    <Locked size={20} />
                  </HeaderGlobalAction>
                )}
                <HeaderGlobalAction aria-label="Profile" onClick={() => navigate("/app/profile")}>
                  <UserAvatar size={20} />
                </HeaderGlobalAction>
                <HeaderGlobalAction aria-label="Sign out" onClick={handleLogout}>
                  <UserFollow size={20} />
                </HeaderGlobalAction>
              </HeaderGlobalBar>

              <SideNav
                aria-label="Side navigation"
                expanded={isSideNavExpanded}
                isPersistent
                onSideNavBlur={onClickSideNavExpand}
                onOverlayClick={() => isSideNavExpanded && onClickSideNavExpand()}
                href="#main-content"
              >
                <SideNavItems>
                  {navItems.map((item) => (
                    <SideNavLink
                      key={item.to}
                      as={NavLink}
                      to={item.to}
                      end={item.to === "/app"}
                      onClick={() => isSideNavExpanded && onClickSideNavExpand()}
                    >
                      {item.label}
                      {item.badge && <span className="queue-badge">!</span>}
                    </SideNavLink>
                  ))}
                </SideNavItems>
              </SideNav>
            </Header>

            <Content id="main-content" className="app-content">
              <Outlet />
            </Content>
          </>
        )}
      />

      <SearchCommand open={searchOpen} onClose={() => setSearchOpen(false)} leagues={LEAGUES} />

      {/* Mounted at shell level rather than per page, so the conversation
          survives navigation between matches -- which is the whole point of
          being able to ask a follow-up about the fixture you just opened. */}
      <ChatDock />
    </LeagueContext.Provider>
  );
}

export { LEAGUES };
