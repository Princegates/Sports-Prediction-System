import type { AdminPick } from "../types";
import { useTilt } from "../lib/useTilt";

const RISK_LABELS: Record<AdminPick["risk_tier"], string> = {
  low: "Low risk",
  medium: "Medium risk",
  high: "High risk",
};

/** A whole multi-leg slip, not a single outcome -- see GudaPickCard for the
 * single-pick equivalent. Not clickable, same reasoning as GudaPickCard: a
 * leg's own match may still be premium-gated, and the combined number is
 * what's being shown off here, not a doorway into any one leg's page. */
export function AdminPickCard({ pick }: { pick: AdminPick }) {
  const tilt = useTilt<HTMLDivElement>();

  return (
    <div className="card match-card tilt-card" ref={tilt.ref} onMouseMove={tilt.onMouseMove} onMouseLeave={tilt.onMouseLeave}>
      <div className="match-card-top">
        <span className="match-competition">
          {pick.label || `${pick.legs.length}-leg slip`}
        </span>
        <span className={`risk-tag ${pick.risk_tier}`}>{RISK_LABELS[pick.risk_tier]}</span>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 6, margin: "8px 0" }}>
        {pick.legs.map((leg) => (
          <div key={`${leg.match_id}-${leg.market}-${leg.selection}`} className="match-meta-row" style={{ justifyContent: "space-between" }}>
            <span>
              {leg.home_team} vs {leg.away_team}
              <span className="sub" style={{ marginLeft: 6 }}>
                {leg.market}: {leg.selection}
              </span>
            </span>
          </div>
        ))}
      </div>

      <div className="match-meta-row tabular-nums" style={{ fontWeight: 600 }}>
        {pick.combined_odds.toFixed(2)} combined odds · {(pick.combined_probability * 100).toFixed(0)}% combined
        probability
      </div>

      {pick.note && (
        <p style={{ margin: "8px 0 0", fontSize: 13, color: "var(--text-secondary)", fontStyle: "italic" }}>
          &ldquo;{pick.note}&rdquo;
        </p>
      )}
    </div>
  );
}
