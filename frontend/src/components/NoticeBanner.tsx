import { useEffect, useState } from "react";
import { fetchNotice } from "../api";

/**
 * A superadmin-authored banner, shown to every visitor -- logged in or not,
 * free tier or premium. Reads Settings -> Site notice; off unless a message
 * is actually set. Dismiss is per-browser-tab only (no server round trip),
 * so it reappears next visit -- appropriate for something meant to be seen,
 * not permanently cleared.
 */
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

  return (
    <div className="site-notice" role="status">
      <span className="site-notice-icon" aria-hidden>
        ◈
      </span>
      <span className="site-notice-text">{message}</span>
      <button className="site-notice-close" onClick={() => setDismissed(true)} aria-label="Dismiss notice">
        ×
      </button>
    </div>
  );
}
