import { NavLink } from "react-router-dom";
import { Home, ChartLine, ShoppingCatalog, AiGenerate } from "@carbon/icons-react";

const ITEMS: { to: string; label: string; end?: boolean; icon?: typeof Home }[] = [
  { to: "/app", label: "Home", end: true, icon: Home },
  { to: "/app/predictions", label: "Picks", icon: ChartLine },
  { to: "/app/markets", label: "Markets", icon: ShoppingCatalog },
  { to: "/app/betcodes", label: "AI Gen", icon: AiGenerate },
  { to: "/app/live", label: "Live" },
];

/** One-tap access to the five pages a member opens most, on a phone where
 * the side nav's hamburger costs an extra tap for every single one of
 * them. Desktop and tablet keep the hamburger-only nav (see AppShell) --
 * this is hidden above the phone breakpoint (styles.css), and doesn't try
 * to hold everything the side nav does: Access/Profile/Admin/Settings and
 * search still live there, reachable in one more tap than these five. */
export function MobileBottomNav() {
  return (
    <nav className="mobile-bottom-nav" aria-label="Primary">
      {ITEMS.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.end}
          className={({ isActive }) => `mobile-bottom-nav-link${isActive ? " active" : ""}`}
        >
          <span className="mobile-bottom-nav-icon" aria-hidden>
            {item.icon ? <item.icon size={20} /> : <span className="mobile-bottom-nav-dot" />}
          </span>
          {item.label}
        </NavLink>
      ))}
    </nav>
  );
}
