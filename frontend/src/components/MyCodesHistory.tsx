import { useEffect, useState } from "react";
import { fetchMyBookingSlips } from "../api";
import type { MyBookingSlip } from "../types";
import { ErrorState } from "./ErrorState";

const RESULT_LABELS: Record<MyBookingSlip["result"], string> = {
  pending: "Pending",
  won: "Won",
  lost: "Lost",
  unresolved: "Confirming result",
};

const LEG_RESULT_ICONS: Record<"won" | "lost" | "unresolved", string> = {
  won: "✓",
  lost: "✗",
  unresolved: "?",
};

function formatKickoff(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    weekday: "short", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

/** A member's own "my codes" results history -- every code they've ever
 * generated through AI Generation / Markets' booking flow, with what
 * actually happened once it settles. Collapsed by default (a toggle, not a
 * separate page) since most visits to this page are about generating a new
 * code, not reviewing old ones. */
export function MyCodesHistory() {
  const [open, setOpen] = useState(false);
  const [slips, setSlips] = useState<MyBookingSlip[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open || slips !== null) return;
    fetchMyBookingSlips()
      .then(setSlips)
      .catch((e) => setError(String(e instanceof Error ? e.message : e)));
  }, [open, slips]);

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
          {slips !== null && slips.length > 0 && (
            <div style={{ display: "grid", gap: 10 }}>
              {slips.map((slip) => (
                <div key={slip.id} className="card card-pad">
                  <div className="section-header" style={{ marginBottom: 6 }}>
                    <span className="sub">{new Date(slip.created_at).toLocaleDateString()}</span>
                    {slip.result !== "pending" && (
                      <span className={`result-tag ${slip.result}`}>{RESULT_LABELS[slip.result]}</span>
                    )}
                  </div>
                  <ol style={{ display: "flex", flexDirection: "column", gap: 6, margin: 0, padding: 0, listStyle: "none" }}>
                    {slip.legs.map((leg, i) => (
                      <li key={`${leg.match_id}-${leg.market}-${leg.selection}`} style={{ fontSize: 13 }}>
                        <span className="tabular-nums" style={{ color: "var(--text-muted)" }}>
                          {i + 1}.
                        </span>{" "}
                        {leg.home_team} vs {leg.away_team}
                        {leg.result && (
                          <span className={`leg-result-icon ${leg.result}`} title={RESULT_LABELS[leg.result]}>
                            {LEG_RESULT_ICONS[leg.result]}
                          </span>
                        )}
                        <div style={{ color: "var(--text-muted)", fontSize: 12 }}>
                          {leg.market}: {leg.selection} · {formatKickoff(leg.kickoff)}
                        </div>
                      </li>
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
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
