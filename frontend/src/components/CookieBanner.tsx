import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

const ACK_KEY = "cookie_notice_ack";

/**
 * One-time notice that the site stores things in the browser's local
 * storage -- not actual cookies (see CookiePolicy.tsx: nothing here sets a
 * cookie, and there is no analytics or advertising script to opt in or out
 * of), but the plain-language disclosure people expect regardless. Shown
 * once per browser; dismissing it just records that, the same
 * "localStorage, defensively" pattern main.tsx already uses for
 * private-browsing/storage-disabled visitors.
 *
 * Mounted once in main.tsx, outside <App/>'s routes, so it covers both the
 * public site and the signed-in product rather than being duplicated into
 * PublicShell and AppShell separately.
 */
export function CookieBanner() {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    try {
      if (localStorage.getItem(ACK_KEY) !== "1") setVisible(true);
    } catch {
      // private-browsing / storage-disabled -- show it every visit rather
      // than not at all; there's nothing to remember the dismissal in.
      setVisible(true);
    }
  }, []);

  function dismiss() {
    try {
      localStorage.setItem(ACK_KEY, "1");
    } catch {
      // nothing to persist -- it'll just show again next visit
    }
    setVisible(false);
  }

  if (!visible) return null;

  return (
    <div className="cookie-banner" role="region" aria-label="Cookie notice">
      <p>
        We use your browser's local storage to keep you signed in and remember things like your theme
        and last-viewed league -- no tracking or advertising cookies. See the{" "}
        <Link to="/cookie-policy">Cookie Policy</Link> for the full list.
      </p>
      <button type="button" className="btn" onClick={dismiss}>
        Got it
      </button>
    </div>
  );
}
