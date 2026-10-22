import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { apiUrl } from "../api";
import { useAuth } from "../lib/AuthContext";
import { Trophy } from "./icons";
import type { WinningSlip } from "../types";

function wonLabel(slip: WinningSlip): string | null {
  if (!slip.won_on) return null;
  return new Date(`${slip.won_on}T12:00:00`).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

/**
 * Screenshots of won SportyBet slips -- posted by the team, or shared by
 * members and approved (Admin -> Winning slips) -- as a thumbnail grid; tap
 * one to see it full size. Shown on the public home page, the member
 * Dashboard and AI Picks.
 */
export function WinningSlipsGallery({ slips, title = "Recent winning slips" }: { slips: WinningSlip[]; title?: string }) {
  const [open, setOpen] = useState<WinningSlip | null>(null);
  const { user } = useAuth();

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  // Signed-in members always see the section, so "Share your win" is there
  // to find even before anything has been posted; a visitor sees it only
  // once there are slips to show.
  if (slips.length === 0 && !user) return null;

  return (
    <div className="wins">
      <div className="wins-head">
        <div className="wins-head-text">
          <h2>{title}</h2>
          <p className="meta" style={{ margin: 0 }}>
            {slips.length > 0
              ? "Won SportyBet slips built on our picks."
              : "No winning slips posted yet -- won with our picks? Be the first to share yours."}
          </p>
        </div>
        <Link className="btn wins-share-btn" to={user ? "/app/share-win" : "/register"}>
          <Trophy size={16} /> Share your win
        </Link>
      </div>
      <div className="wins-grid">
        {slips.map((s) => (
          <button key={s.id} type="button" className="wins-thumb" onClick={() => setOpen(s)} aria-label={`View ${s.caption || "winning slip"}`}>
            <img
              src={apiUrl(s.image_path)}
              alt={s.caption || "Winning SportyBet slip"}
              loading="lazy"
              width={s.width ?? undefined}
              height={s.height ?? undefined}
            />
            {s.from_member && <span className="wins-member-tag">Shared by a member</span>}
            {(s.caption || s.won_on) && (
              <span className="wins-caption">
                {s.caption}
                {wonLabel(s) && <span className="meta"> · {wonLabel(s)}</span>}
              </span>
            )}
          </button>
        ))}
      </div>

      {open && (
        <div className="wins-lightbox" role="dialog" aria-modal="true" aria-label={open.caption || "Winning slip"} onClick={() => setOpen(null)}>
          <figure onClick={(e) => e.stopPropagation()}>
            <img src={apiUrl(open.image_path)} alt={open.caption || "Winning SportyBet slip"} />
            {(open.caption || open.won_on) && (
              <figcaption>
                {open.caption}
                {wonLabel(open) && <span className="meta"> · Won {wonLabel(open)}</span>}
              </figcaption>
            )}
            <button type="button" className="btn ghost btn-sm" onClick={() => setOpen(null)}>
              Close
            </button>
          </figure>
        </div>
      )}
    </div>
  );
}
