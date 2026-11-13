import { useState } from "react";
import { shareSelection } from "../api";
import type { BetCodePick } from "../types";
import { CopyButton } from "./CopyButton";

/**
 * "Share" for a generated slip or a saved selection. Makes a link to the picks
 * (a copy taken now). Whoever opens it -- a member with access -- sees the games,
 * can change them and then makes their own booking code; nothing they do changes
 * the sender's slip. The link works for 14 days.
 */

const LINK_DAYS = 14;

export function shareUrl(token: string): string {
  return `${window.location.origin}/app/shared/${token}`;
}

export function ShareSelectionControl({
  picks,
  name,
  small = false,
}: {
  picks: BetCodePick[];
  /** Name for the shared slip; blank gets a dated one. */
  name?: string;
  /** Compact button for list rows. */
  small?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [link, setLink] = useState<{ url: string; note: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleShare() {
    setBusy(true);
    setError(null);
    try {
      const shared = await shareSelection(picks, name);
      setLink({
        url: shareUrl(shared.token),
        note: shared.warnings.length > 0 ? shared.warnings.join(" ") : "",
      });
    } catch (err) {
      setError(String(err instanceof Error ? err.message : err));
    } finally {
      setBusy(false);
    }
  }

  const message = link
    ? `Have a look at this slip on Socca Intelligence. You can change it before making your own code: ${link.url}`
    : "";
  const canNativeShare = typeof navigator !== "undefined" && typeof navigator.share === "function";

  return (
    <>
      {!link && (
        <button
          type="button"
          className="btn ghost"
          style={small ? { padding: "4px 10px", fontSize: 13 } : undefined}
          disabled={busy}
          onClick={handleShare}
        >
          {busy ? "Making link…" : "Share"}
        </button>
      )}
      {link && (
        <div className="share-box" role="status">
          <p style={{ margin: "0 0 6px" }}>
            <strong>Link ready.</strong> Send it to a friend. They can open it, change the games, then make
            their own code. Your slip stays as it is. It works for {LINK_DAYS} days.
          </p>
          {link.note && <p className="setting-note" style={{ margin: "0 0 6px" }}>{link.note}</p>}
          <input
            readOnly
            aria-label="Share link"
            value={link.url}
            onFocus={(e) => e.currentTarget.select()}
            style={{ width: "100%", marginBottom: 8 }}
          />
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <CopyButton text={link.url} label="Copy link" className="btn" />
            <a
              className="btn ghost"
              href={`https://wa.me/?text=${encodeURIComponent(message)}`}
              target="_blank"
              rel="noopener noreferrer"
            >
              Send on WhatsApp
            </a>
            {canNativeShare && (
              <button
                type="button"
                className="btn ghost"
                onClick={() => void navigator.share({ title: "A slip to look at", text: message, url: link.url }).catch(() => {})}
              >
                More ways…
              </button>
            )}
            <button type="button" className="btn ghost" onClick={() => setLink(null)}>
              Done
            </button>
          </div>
        </div>
      )}
      {error && (
        <p className="auth-error" role="alert" style={{ flexBasis: "100%", margin: 0 }}>
          {error}
        </p>
      )}
    </>
  );
}
