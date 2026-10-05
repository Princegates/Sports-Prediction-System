import { createContext, type Dispatch, type SetStateAction, useContext, useEffect, useState } from "react";
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
import { Search, UserAvatar, Settings as SettingsIcon, Locked, UserFollow, Pin, PinFilled, ChevronRight } from "@carbon/icons-react";
import { SearchCommand } from "./SearchCommand";
import { ChatDock } from "./ChatDock";
import { MobileBottomNav } from "./MobileBottomNav";
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

const SIDENAV_PIN_STORAGE_KEY = "app_sidenav_pinned";

/** Pinning the desktop nav overrides the hover-reveal rail (below) back to
 * always-expanded, for anyone who navigates often enough that re-hovering
 * every time is more friction than the screen space it buys back. */
function readStoredPinned(): boolean {
  try {
    return localStorage.getItem(SIDENAV_PIN_STORAGE_KEY) === "1";
  } catch {
    return false;
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

interface AppShellHeaderProps {
  // Supplied by HeaderContainer itself.
  isSideNavExpanded: boolean;
  onClickSideNavExpand: () => void;
  // Everything else is forwarded through HeaderContainer's `render` prop as
  // plain extra props (see the component below for why).
  league: string;
  setLeague: (l: string) => void;
  pinned: boolean;
  setPinned: Dispatch<SetStateAction<boolean>>;
  onSearchOpen: () => void;
  isSuperadmin: boolean;
  needsAccess: boolean;
  navItems: NavItem[];
  onNavigate: (path: string) => void;
  onLogout: () => void;
}

/** The header + side nav + page content, as its own module-level component.
 *
 * Carbon's HeaderContainer takes this via a `render` prop and, under the
 * hood, renders it as `<Children {...props} />` -- i.e. it treats whatever
 * you pass as a *component type*, not a plain callback. Writing that prop as
 * an inline arrow function in AppShell's own JSX (as this used to) hands
 * HeaderContainer a brand-new function -- a brand-new component type as far
 * as React's reconciler is concerned -- on every single AppShell re-render.
 * React then tears down and rebuilds this entire subtree, Outlet and
 * everything the current page mounted, on every re-render -- which includes
 * the fetch every one of those mounts kicks off, which itself updates state
 * that triggers the next AppShell re-render. A self-sustaining loop at
 * network-request speed, with no page navigation or reload needed to see
 * it. Defining it here, at module scope, keeps its identity permanently
 * stable so HeaderContainer sees the same component on every render and
 * only patches props -- normal React, no remount.
 */
function AppShellHeader({
  isSideNavExpanded,
  onClickSideNavExpand,
  league,
  setLeague,
  pinned,
  setPinned,
  onSearchOpen,
  isSuperadmin,
  needsAccess,
  navItems,
  onNavigate,
  onLogout,
}: AppShellHeaderProps) {
  return (
    <>
      <Header aria-label="Socca Intelligence">
        <SkipToContent />
        <HeaderMenuButton
          aria-label={isSideNavExpanded ? "Close menu" : "Open menu"}
          onClick={onClickSideNavExpand}
          isActive={isSideNavExpanded}
          // isCollapsible={false} (not the true/shorthand this had)
          // is what tells HeaderMenuButton to add its own built-in
          // ${prefix}--header__menu-toggle__hidden class, hiding the
          // button above the same lg breakpoint SideNav's own
          // isPersistent already shows the nav at unconditionally.
          // With isCollapsible true, the button stayed visible and
          // clickable on desktop, where clicking it flipped on the
          // dismiss overlay meant for the mobile drawer behind an
          // already-visible, unrelated sidebar -- with no visible
          // change to explain what had just happened.
          isCollapsible={false}
        />
        <HeaderName as={NavLink} to="/app" prefix="">
          {/* The full wordmark and the league select together need more
              width than a phone screen has -- shortened here rather
              than letting the header overflow and clip its own
              right-side icons off the edge (see the phone breakpoint
              that also shrinks .app-league-select). */}
          <span className="app-brand-full">Socca Intelligence</span>
          <span className="app-brand-short">Socca</span>
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
          <HeaderGlobalAction aria-label="Search teams" onClick={onSearchOpen}>
            <Search size={20} />
          </HeaderGlobalAction>
          {isSuperadmin && (
            <HeaderGlobalAction aria-label="Admin" onClick={() => onNavigate("/app/admin")}>
              <SettingsIcon size={20} />
            </HeaderGlobalAction>
          )}
          {needsAccess && (
            <HeaderGlobalAction aria-label="Redeem access" onClick={() => onNavigate("/app/access")}>
              <Locked size={20} />
            </HeaderGlobalAction>
          )}
          <HeaderGlobalAction aria-label="Profile" onClick={() => onNavigate("/app/profile")}>
            <UserAvatar size={20} />
          </HeaderGlobalAction>
          <HeaderGlobalAction aria-label="Sign out" onClick={onLogout}>
            <UserFollow size={20} />
          </HeaderGlobalAction>
        </HeaderGlobalBar>

        <SideNav
          aria-label="Side navigation"
          expanded={isSideNavExpanded || pinned}
          isPersistent
          isRail
          className="app-sidenav-rail"
          onSideNavBlur={onClickSideNavExpand}
          onOverlayClick={() => isSideNavExpanded && onClickSideNavExpand()}
          href="#main-content"
        >
          <div className="app-sidenav-pin-row">
            <button
              type="button"
              className="app-sidenav-pin-btn"
              aria-pressed={pinned}
              aria-label={pinned ? "Unpin sidebar" : "Pin sidebar open"}
              onClick={() => setPinned((v) => !v)}
            >
              {pinned ? <PinFilled size={16} /> : <Pin size={16} />}
              {pinned ? "Pinned open" : "Pin open"}
            </button>
          </div>
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

        {/* Purely a hint that the thin edge rail (collapsed by default
            on desktop) is a nav, not decoration -- hovering it already
            expands the real SideNav above without this; clicking it
            is a shortcut to pin it open instead of hovering to find
            out. Hidden once pinned, since the nav is then always
            expanded and there's nothing left to hint at. */}
        {!pinned && (
          <button
            type="button"
            className="app-sidenav-hint"
            aria-label="Show sidebar navigation"
            onClick={() => setPinned(true)}
          >
            <ChevronRight size={14} />
          </button>
        )}
      </Header>

      <Content id="main-content" className={`app-content${pinned ? " app-content--nav-pinned" : ""}`}>
        <Outlet />
      </Content>

      <MobileBottomNav />
    </>
  );
}

export function AppShell() {
  // "" ("All leagues") on purpose, not LEAGUES[0] -- Dashboard and Live
  // degrade to an unfiltered (cross-league) query just fine when league is
  // falsy, and AI Generation previously defaulted to "any league" before it
  // was wired to this shared selector, so defaulting here to one specific
  // league would have silently narrowed what it searches on first load.
  const [league, setLeague] = useState(readStoredLeague);
  const [searchOpen, setSearchOpen] = useState(false);
  const [pinned, setPinned] = useState(readStoredPinned);
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

  useEffect(() => {
    try {
      localStorage.setItem(SIDENAV_PIN_STORAGE_KEY, pinned ? "1" : "0");
    } catch {
      // private-browsing / storage-disabled -- just won't persist across reloads
    }
  }, [pinned]);

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
        league={league}
        setLeague={setLeague}
        pinned={pinned}
        setPinned={setPinned}
        onSearchOpen={() => setSearchOpen(true)}
        isSuperadmin={user?.role === "superadmin"}
        needsAccess={needsAccess}
        navItems={navItems}
        onNavigate={navigate}
        onLogout={handleLogout}
        render={AppShellHeader}
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
