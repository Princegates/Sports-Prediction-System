import { useEffect, useState } from "react";
import { apiUrl, fetchWinningSlipImageData } from "../api";
import type { WinningSlip } from "../types";

/** A winning slip's thumbnail in the review/own-submissions lists. A live
 * slip loads from its public URL; one that isn't public yet (pending, or
 * hidden) is fetched as a data: URL by its owner or an admin. */
export function WinningSlipThumb({ slip, asAdmin }: { slip: WinningSlip; asAdmin: boolean }) {
  const [src, setSrc] = useState<string | null>(slip.status === "live" ? apiUrl(slip.image_path) : null);

  useEffect(() => {
    if (slip.status === "live") {
      setSrc(apiUrl(slip.image_path));
      return;
    }
    let cancelled = false;
    fetchWinningSlipImageData(slip.id, asAdmin)
      .then((r) => !cancelled && setSrc(r.data_url))
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [slip.id, slip.status, slip.image_path, asAdmin]);

  return src ? (
    <img src={src} alt={slip.caption || "Winning slip"} loading="lazy" />
  ) : (
    <div className="wins-admin-hidden">Loading…</div>
  );
}
