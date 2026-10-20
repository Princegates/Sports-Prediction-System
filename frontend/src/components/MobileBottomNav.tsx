import { NavLink } from "react-router-dom";
import { Home, ChartLine, ShoppingCatalog, AiGenerate, Live as LiveIcon, PinFilled } from "./icons";

const ITEMS: { to: string; label: string; end?: boolean; icon?: typeof Home }[] = [
  { to: "/app", label: "Home", end: true, icon: Home },
  { to: "/app/ai-picks", label: "AI Picks", icon: PinFilled },
  { to: "/app/predictions", label: "Picks", icon: ChartLine },
  { to: "/app/markets", label: "Markets", icon: ShoppingCatalog },
  { to: "/app/betcodes", label: "AI Gen", icon: AiGenerate },
  { to: "/app/live", label: "Live", icon: LiveIcon },
];

/** One-tap access to the six pages a member opens most, on a phone where
 * the top tab row (AppShell's own .topbar-tabs) is hidden below the phone
 * breakpoint -- a horizontally-scrolling pill row squeezed in beside the
 * brand, league select and icon cluster reads worse than this dedicated
 * strip does, and doesn't give each destination room for an icon. Desktop
 * and tablet keep the top tab row instead (see AppShell) -- this is hidden
 * above the phone breakpoint (styles.css), and doesn't try to hold
 * everything the top row does: Access/Profile/Admin/Settings and search
 * still live in the topbar's avatar menu/icons, reachable there same as
 * always. */
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
