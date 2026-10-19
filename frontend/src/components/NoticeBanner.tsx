import { useEffect, useState } from "react";
import { fetchNotice } from "../api";

// Roughly constant scroll speed regardless of message length -- a fixed
// duration would crawl for a short message and race for a long one, since
// both travel the same 50% of the (message-length-dependent) track width
// in that same fixed time. CHAR_WIDTH_PX is a rough estimate for this
// banner's ~13.5px text, not a measured value -- close enough that the
// speed reads as consistent; it doesn't need to be exact. Clamped so a
// very short message doesn't whip past unreadably fast and a very long
// one doesn't take forever to loop.
const PIXELS_PER_SECOND = 55;
const CHAR_WIDTH_PX = 7;
const GAP_PX = 48;
const MIN_DURATION_S = 10;
const MAX_DURATION_S = 40;

/** A superadmin-authored banner, shown to every visitor -- logged in or not,
 * free tier or premium. Reads Settings -> Site notice; off unless a message
 * is actually set. Dismiss is per-browser-tab only (no server round trip),
 * so it reappears next visit -- appropriate for something meant to be seen,
 * not permanently cleared.
 *
 * Scrolls continuously (marquee-style) rather than wrapping or truncating:
 * a one-line banner that wrapped to 2-3 lines on a phone ate real Dashboard
 * space for a message that's supposed to be a passing heads-up, and an
 * ellipsis just hid whatever didn't fit. The message is duplicated back to
 * back and the pair scrolled left by exactly half the track's width (one
 * copy + the gap to the next) -- the loop point is seamless because that's
 * exactly where the second copy already sits, so it reads as infinite, not
 * a visible snap-back. Paused on hover/focus to read it at rest, and
 * disabled entirely under prefers-reduced-motion, same as the rest of the
 * app's other looping animations (route-spinner, the mascot). */
export function NoticeBanner() {
  const [message, setMessage] = useState<string | null>(null);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchNotice()
      .then((n) => !cancelled && setMessage(n.enabled ? n.message : null))
      .catch(() => {
        // Best-effort -- a notice that fails to load should never block the page.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!message || dismissed) return null;

  const estimatedTrackPx = message.length * CHAR_WIDTH_PX + GAP_PX;
  const durationS = Math.min(MAX_DURATION_S, Math.max(MIN_DURATION_S, estimatedTrackPx / PIXELS_PER_SECOND));

  return (
    <div className="site-notice" role="status">
      <span className="site-notice-icon" aria-hidden>
        ◈
      </span>
      <div className="site-notice-track">
        <div className="site-notice-marquee" style={{ animationDuration: `${durationS}s` }}>
          <span className="site-notice-text">{message}</span>
          <span className="site-notice-text" aria-hidden="true">
            {message}
          </span>
        </div>
      </div>
      <button className="site-notice-close" onClick={() => setDismissed(true)} aria-label="Dismiss notice">
        ×
      </button>
    </div>
  );
}
