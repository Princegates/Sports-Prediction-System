import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link, NavLink } from "react-router-dom";
import { useAuth } from "../lib/AuthContext";

/**
 * Chrome for the public marketing pages.
 *
 * Separate from AppShell rather than a variant of it: AppShell assumes a
 * logged-in user (league selector, sidebar nav, per-account theme sync),
 * and threading "but not when anonymous" through all of that would make
 * both harder to read than two components are.
 *
 * Theme is still respected here so a visitor who set light mode, signed out
 * and came back doesn't get flashed a dark page.
 */

type Theme = "dark" | "light";

function readStoredTheme(): Theme {
  try {
    return (localStorage.getItem("theme") as Theme) || "dark";
  } catch {
    return "dark";
  }
}

const PUBLIC_NAV = [
  { to: "/", label: "Home" },
  { to: "/how-it-works", label: "How it works" },
  { to: "/responsible", label: "Responsible use" },
];

export function PublicShell({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const [theme, setTheme] = useState<Theme>(readStoredTheme);
  const [menuOpen, setMenuOpen] = useState(false);
  const firstRun = useRef(true);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    if (firstRun.current) {
      firstRun.current = false;
      return;
    }
    try {
      localStorage.setItem("theme", theme);
    } catch {
      // storage disabled -- the choice just won't persist
    }
  }, [theme]);

  return (
    <div className="public-shell">
      <header className="public-topbar">
        <Link to="/" className="brand" onClick={() => setMenuOpen(false)}>
          <span className="brand-mark">AI</span>
          <span className="brand-text">
            <strong>Match Intelligence</strong>
            <span>Football AI</span>
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

        <div className="public-actions">
          <button
            className="btn ghost"
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            aria-label={theme === "dark" ? "Switch to day theme" : "Switch to night theme"}
          >
            {theme === "dark" ? "☀" : "☾"}
          </button>

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

      <main className="public-main">{children}</main>

      <footer className="public-footer">
        <div className="public-footer-grid">
          <div className="public-footer-brand">
            <span className="brand-mark">AI</span>
            <div>
              <strong>Match Intelligence</strong>
              <p>
                Ensemble football prediction with calibrated probabilities, explainable factors and an
                auditable confidence score on every output.
              </p>
            </div>
          </div>

          <div className="public-footer-col">
            <h4>Platform</h4>
            <Link to="/how-it-works">How it works</Link>
            <Link to="/register">Request access</Link>
            <Link to="/account-status">Check application status</Link>
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
            <a href="https://www.begambleaware.org" target="_blank" rel="noreferrer noopener">
              BeGambleAware
            </a>
            <a href="https://www.gamblersanonymous.org" target="_blank" rel="noreferrer noopener">
              Gamblers Anonymous
            </a>
            <span className="public-footer-note">Helpline (US): 1-800-522-4700</span>
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
        </div>
      </footer>
    </div>
  );
}
