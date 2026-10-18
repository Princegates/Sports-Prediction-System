import { useEffect, useState } from "react";
import { fetchMyBookingSlips, promoteBookingSlipToTrackRecord } from "../api";
import { useAuth } from "../lib/AuthContext";
import type { MyBookingSlip } from "../types";
import { ErrorState } from "./ErrorState";
import { LedgerRow } from "./LedgerRow";

const RESULT_LABELS: Record<MyBookingSlip["result"], string> = {
  pending: "Pending",
  won: "Won",
  lost: "Lost",
  unresolved: "Confirming result",
};

/** A member's own "my codes" results history -- every settled code they've
 * generated through AI Generation / Markets' booking flow, with what
 * actually happened. A still-pending code (its matches haven't finished)
 * has nothing to show here yet, so it's filtered out rather than listed
 * with no result. Collapsed by default (a toggle, not a separate page)
 * since most visits to this page are about generating a new code, not
 * reviewing old ones. */
export function MyCodesHistory() {
  const { user } = useAuth();
  const [open, setOpen] = useState(false);
  const [slips, setSlips] = useState<MyBookingSlip[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [publishingId, setPublishingId] = useState<number | null>(null);
  const [publishedIds, setPublishedIds] = useState<Set<number>>(new Set());

  useEffect(() => {
    if (!open || slips !== null) return;
    fetchMyBookingSlips()
      .then(setSlips)
      .catch((e) => setError(String(e instanceof Error ? e.message : e)));
  }, [open, slips]);

  // Only settled codes -- a still-pending one (its matches haven't finished)
  // has no result to show yet, so it's noise here rather than history.
  const settledSlips = slips?.filter((slip) => slip.result !== "pending") ?? null;

  async function handlePublish(slipId: number) {
    setPublishingId(slipId);
    try {
      await promoteBookingSlipToTrackRecord(slipId);
      setPublishedIds((prev) => new Set(prev).add(slipId));
    } catch (err) {
      window.alert(String(err instanceof Error ? err.message : err));
    } finally {
      setPublishingId(null);
    }
  }

  return (
    <div className="card card-pad" style={{ marginTop: 20 }}>
      <button
        type="button"
        className="btn ghost"
        style={{ width: "100%", justifyContent: "space-between", display: "flex" }}
        onClick={() => setOpen((o) => !o)}
      >
        <span>My codes -- results history</span>
        <span aria-hidden>{open ? "▲" : "▼"}</span>
      </button>

      {open && (
        <div style={{ marginTop: 14 }}>
          {error && <ErrorState message={error} />}
          {!error && slips === null && <p className="sub">Loading your codes…</p>}
          {slips !== null && slips.length === 0 && (
            <p className="sub">No codes generated yet -- book one above and it'll show up here with its result once the match finishes.</p>
          )}
          {slips !== null && slips.length > 0 && settledSlips !== null && settledSlips.length === 0 && (
            <p className="sub">No settled codes yet -- results show up here once a code's matches finish.</p>
          )}
          {settledSlips !== null && settledSlips.length > 0 && (
            <div style={{ display: "grid", gap: 10 }}>
              {settledSlips.map((slip) => (
                <div key={slip.id} className="card card-pad">
                  <div className="section-header" style={{ marginBottom: 6 }}>
                    <span className="sub">{new Date(slip.created_at).toLocaleDateString()}</span>
                    <span className={`result-tag ${slip.result}`}>{RESULT_LABELS[slip.result]}</span>
                  </div>
                  <ol style={{ margin: 0, padding: 0 }}>
                    {slip.legs.map((leg, i) => (
                      <LedgerRow key={`${leg.match_id}-${leg.market}-${leg.selection}`} leg={leg} index={i + 1} />
                    ))}
                  </ol>
                  {slip.site_codes.some((c) => c.status === "code_ready") && (
                    <div className="sub" style={{ marginTop: 8 }}>
                      {slip.site_codes
                        .filter((c) => c.status === "code_ready")
                        .map((c) => `${c.name}: ${c.code}`)
                        .join(" · ")}
                    </div>
                  )}
                  {user?.role === "superadmin" && (slip.result === "won" || slip.result === "lost") && (
                    <div style={{ marginTop: 8 }}>
                      {publishedIds.has(slip.id) ? (
                        <span className="sub">Published to the public track record.</span>
                      ) : (
                        <button
                          type="button"
                          className="btn ghost"
                          style={{ padding: "2px 8px", fontSize: 12 }}
                          disabled={publishingId === slip.id}
                          onClick={() => handlePublish(slip.id)}
                          title="Shows this slip -- win or loss -- on the public track record page."
                        >
                          Publish to track record
                        </button>
                      )}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
