import { useState, type ReactNode } from "react";
import { Link, NavLink } from "react-router-dom";
import { useAuth } from "../lib/AuthContext";
import { ThemeToggle } from "./ThemeToggle";
import { usePageMeta } from "../lib/pageMeta";

/**
 * Chrome for the public marketing pages.
 *
 * Separate from AppShell rather than a variant of it: AppShell assumes a
 * logged-in user (league selector, sidebar nav), and threading "but not
 * when anonymous" through all of that would make both harder to read than
 * two components are.
 *
 * Accent is admin-controlled site-wide, with no toggle here. Theme
 * (day/night) is admin-controlled by default but a visitor can override it
 * for themselves with ThemeToggle in the header -- see main.tsx for how
 * that choice is persisted and kept from being overwritten by the
 * site-wide default.
 *
 * ``title``/``description`` set this page's document title and meta
 * description (see lib/pageMeta) -- required, since every page using this
 * shell is one of the ones public/sitemap.xml lists for indexing, and each
 * needs its own specific search-result snippet rather than sharing one.
 */

const PUBLIC_NAV = [
  { to: "/", label: "Home" },
  { to: "/how-it-works", label: "How it works" },
  { to: "/responsible", label: "Responsible use" },
];

export function PublicShell({
  children, title, description,
}: {
  children: ReactNode;
  title: string;
  description: string;
}) {
  usePageMeta(title, description);
  const { user } = useAuth();
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <div className="public-shell">
      <header className="public-topbar">
        <Link to="/" className="brand" onClick={() => setMenuOpen(false)}>
          <span className="brand-mark">SI</span>
          <span className="brand-text">
            <strong>Socca Intelligence</strong>
            <span>Football prediction AI</span>
          </span>
        </Link>

        <nav className="public-nav">
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

        <ThemeToggle />

        <div className="public-actions">
          {user ? (
            <Link className="btn" to="/app">
              Open dashboard
            </Link>
          ) : (
            <>
              <Link className="btn ghost" to="/login">
                Sign in
              </Link>
              <Link className="btn" to="/register">
                Request access
              </Link>
            </>
          )}
        </div>

        <button
          className="public-menu-toggle"
          onClick={() => setMenuOpen(!menuOpen)}
          aria-label="Menu"
          aria-expanded={menuOpen}
        >
          {menuOpen ? "✕" : "☰"}
        </button>
      </header>

      {menuOpen && (
        <div className="public-mobile-menu">
          {PUBLIC_NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
              className="public-nav-link"
              onClick={() => setMenuOpen(false)}
            >
              {item.label}
            </NavLink>
          ))}
          <div className="public-mobile-actions">
            {user ? (
              <Link className="btn" to="/app" onClick={() => setMenuOpen(false)}>
                Open dashboard
              </Link>
            ) : (
              <>
                <Link className="btn ghost" to="/login" onClick={() => setMenuOpen(false)}>
                  Sign in
                </Link>
                <Link className="btn" to="/register" onClick={() => setMenuOpen(false)}>
                  Request access
                </Link>
              </>
            )}
          </div>
        </div>
      )}

      <main className="public-main">
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
            <Link to="/register">Create an account</Link>
            <Link to="/login">Redeem access code</Link>
            <Link to="/account-status">Check your access status</Link>
            <Link to="/login">Sign in</Link>
          </div>

          <div className="public-footer-col">
            <h4>Transparency</h4>
            <Link to="/how-it-works">Model methodology</Link>
            <Link to="/responsible">Responsible use</Link>
            <a
              href="https://github.com/openfootball/football.json"
              target="_blank"
              rel="noreferrer noopener"
            >
              Data source: openfootball
            </a>
            <a href="https://www.football-data.co.uk/data.php" target="_blank" rel="noreferrer noopener">
              Data source: football-data.co.uk
            </a>
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
            Isotonic calibration.
          </p>
          <p className="public-footer-copy">&copy; 2026 Soccaintel.com. All rights reserved. Powered by Anknovate IT Services.</p>
        </div>
      </footer>
    </div>
  );
}
