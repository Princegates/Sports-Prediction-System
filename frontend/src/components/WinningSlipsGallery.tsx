import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { apiUrl } from "../api";
import type { WinningSlip } from "../types";

function wonLabel(slip: WinningSlip): string | null {
  if (!slip.won_on) return null;
  return new Date(`${slip.won_on}T12:00:00`).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

/**
 * Screenshots of won SportyBet slips an admin has posted (Admin -> Winning
 * slips), as a thumbnail grid; tap one to see it full size. Shown on the
 * public home page and the member Dashboard.
 *
 * Always paired with a pointer to the full graded record: a gallery of wins
 * on its own says nothing about the losses, and the site's whole pitch is
 * that it shows both.
 */
export function WinningSlipsGallery({ slips, title = "Recent winning slips" }: { slips: WinningSlip[]; title?: string }) {
  const [open, setOpen] = useState<WinningSlip | null>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  if (slips.length === 0) return null;

  return (
    <div className="wins">
      <div className="wins-head">
        <h2>{title}</h2>
        <p className="meta" style={{ margin: 0 }}>
          Won SportyBet slips built on our picks. Wins are only half the story -- see the{" "}
          <Link to="/#track-record">full track record</Link>, losses included.
        </p>
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
