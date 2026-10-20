import { useState, type ReactNode } from "react";
import { Link, NavLink, useNavigate } from "react-router-dom";
import { Login as LoginIcon, Menu, Close } from "./icons";
import { useAuth } from "../lib/AuthContext";
import { ThemeToggle } from "./ThemeToggle";
import { usePageMeta } from "../lib/pageMeta";

/**
 * Chrome for the public marketing pages.
 *
 * Separate from AppShell rather than a variant of it: AppShell assumes a
 * logged-in user (league selector, account menu), and threading "but not
 * when anonymous" through all of that would make both harder to read than
 * two components are.
 *
 * Theme (day/night) is admin-controlled by default but a visitor can
 * override it for themselves with ThemeToggle in the header -- see
 * main.tsx for how that choice is persisted and kept from being
 * overwritten by the site-wide default.
 *
 * ``title``/``description`` set this page's document title and meta
 * description (see lib/pageMeta) -- required, since every page using this
 * shell is one of the ones public/sitemap.xml lists for indexing, and each
 * needs its own specific search-result snippet rather than sharing one.
 */

const PUBLIC_NAV = [
  { to: "/", label: "Home" },
  { to: "/how-it-works", label: "How it works" },
  { to: "/pricing", label: "Pricing" },
  { to: "/fixtures", label: "Fixtures" },
  { to: "/responsible", label: "Responsible use" },
];

export function PublicShell({
  children, title, description, image,
}: {
  children: ReactNode;
  title: string;
  description: string;
  /** Absolute URL of this page's share image; defaults to the site card. */
  image?: string;
}) {
  usePageMeta(title, description, image);
  const { user } = useAuth();
  const navigate = useNavigate();
  const [drawerOpen, setDrawerOpen] = useState(false);

  function go(path: string) {
    setDrawerOpen(false);
    navigate(path);
  }

  return (
    <div className="public-shell">
      <header className="topbar" aria-label="Socca Intelligence">
        <NavLink to="/" className="topbar-brand">
          <span className="topbar-brand-mark">SI</span>
          <span className="topbar-brand-full">Socca Intelligence</span>
          <span className="topbar-brand-short">Socca</span>
        </NavLink>

        <nav className="public-nav" aria-label="Primary">
          {PUBLIC_NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
              className={({ isActive }) => `public-nav-link${isActive ? " active" : ""}`}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>

        <span className="topbar-spacer" />

        <div className="topbar-actions">
          <ThemeToggle />
          {user ? (
            <button type="button" className="topbar-icon-btn" aria-label="Open dashboard" onClick={() => navigate("/app")}>
              <LoginIcon size={18} />
            </button>
          ) : (
            <>
              <button type="button" className="topbar-icon-btn" aria-label="Sign in" onClick={() => navigate("/login")}>
                <LoginIcon size={18} />
              </button>
              {/* A labelled button, not a bare person icon: it's the one
                  action a first-time visitor is here to take. */}
              <button type="button" className="btn btn-sm topbar-trial-btn" onClick={() => navigate("/register")}>
                Free trial
              </button>
            </>
          )}
          <button
            type="button"
            className="topbar-menu-btn"
            aria-label={drawerOpen ? "Close menu" : "Open menu"}
            aria-expanded={drawerOpen}
            onClick={() => setDrawerOpen((v) => !v)}
          >
            {drawerOpen ? <Close size={20} /> : <Menu size={20} />}
          </button>
        </div>
      </header>

      {drawerOpen && (
        <div className="public-drawer" onClick={() => setDrawerOpen(false)}>
          <div className="public-drawer-panel" onClick={(e) => e.stopPropagation()}>
            {PUBLIC_NAV.map((item) => (
              <NavLink key={item.to} to={item.to} end={item.to === "/"} onClick={() => setDrawerOpen(false)}>
                {item.label}
              </NavLink>
            ))}
            {user ? (
              <button type="button" onClick={() => go("/app")}>Open dashboard</button>
            ) : (
              <>
                <button type="button" onClick={() => go("/login")}>Sign in</button>
                <button type="button" onClick={() => go("/register")}>Request access</button>
              </>
            )}
          </div>
        </div>
      )}

      <main className="public-main" id="main-content">
        {children}
      </main>

      <footer className="public-footer">
        <div className="public-footer-grid">
          <div className="public-footer-brand">
            <span className="brand-mark">SI</span>
            <div>
              <strong>Socca Intelligence</strong>
              <p>
                Ensemble football prediction with calibrated probabilities, explainable factors and an
                auditable confidence score on every output.
              </p>
            </div>
          </div>

          <div className="public-footer-col">
            <h4>Platform</h4>
            <Link to="/how-it-works">How it works</Link>
            <Link to="/pricing">Pricing</Link>
            <Link to="/register">Create an account</Link>
            <Link to="/login">Redeem access code</Link>
            <Link to="/account-status">Check your access status</Link>
            <Link to="/login">Sign in</Link>
          </div>

          <div className="public-footer-col">
            <h4>Transparency</h4>
            <Link to="/how-it-works">Model methodology</Link>
            <Link to="/responsible">Responsible use</Link>
          </div>

          <div className="public-footer-col">
            <h4>Get help</h4>
            {/* Ghana's own line, not a US 1-800 number -- that cannot be
                dialled from here at all, so it read as help while being none. */}
            <a href="https://www.gamblingtherapy.org" target="_blank" rel="noreferrer noopener">
              Gambling Therapy
            </a>
            <a href="https://www.gamblersanonymous.org" target="_blank" rel="noreferrer noopener">
              Gamblers Anonymous
            </a>
            <span className="public-footer-note">Mental Health Authority: 0800 678 678 (toll-free in Ghana)</span>
          </div>
        </div>

        <div className="public-footer-legal">
          <p>
            <strong>Predictions are model probabilities, never guarantees.</strong> Outputs are derived
            from historical data and validated by backtesting; they describe likelihood, not certainty.
            Nothing here is financial advice. 18+.
          </p>
          <p className="public-footer-copy">
            Built on free and open data sources. Elo · Dixon-Coles Poisson · Gradient Boosting ·
            Held-out validation.
          </p>
          <p className="public-footer-copy">
            &copy; 2026 Soccaintel.com. All rights reserved. Powered by Anknovate IT Services. ·{" "}
            <Link to="/cookie-policy">Cookie Policy</Link>
          </p>
        </div>
      </footer>
    </div>
  );
}
