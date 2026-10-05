import { Link } from "react-router-dom";
import type { FeaturedPick } from "../types";
import { useTilt } from "../lib/useTilt";
import { ProbabilityBar } from "./ProbabilityBar";

const RESULT_LABELS: Record<"won" | "lost" | "unresolved", string> = {
  won: "Won",
  lost: "Lost",
  unresolved: "Confirming result",
};

/** Not clickable, same reasoning as FreePickCard -- the match behind it may
 * still be premium, and the card's own probability is what's being shown
 * off here, not a doorway into the full breakdown.
 *
 * ``onSetResult`` is only ever passed by GudaPicksSection for a superadmin
 * viewer, and only rendered as a control when the pick is "unresolved" --
 * the fallback for a market app.outcomes.grading can't auto-grade. */
export function GudaPickCard({
  pick, onSetResult, busy,
}: {
  pick: FeaturedPick;
  onSetResult?: (pickId: number, result: "won" | "lost") => void;
  busy?: boolean;
}) {
  const tilt = useTilt<HTMLDivElement>();

  if (pick.locked) {
    return (
      <div className="card match-card match-card-static tilt-card" ref={tilt.ref} onMouseMove={tilt.onMouseMove} onMouseLeave={tilt.onMouseLeave}>
        <div className="match-card-top">
          <span className="match-competition">{pick.match.league}</span>
        </div>
        <div className="match-teams">
          <span className="team-name">{pick.match.home_team.name}</span>
          <span className="vs-divider">vs</span>
          <span className="team-name away">{pick.match.away_team.name}</span>
        </div>
        <div className="match-meta-row">
          {new Date(pick.match.date).toLocaleString(undefined, { weekday: "short", hour: "2-digit", minute: "2-digit" })}
        </div>
        <div style={{ textAlign: "center", padding: "20px 0 4px" }}>
          <div style={{ fontSize: 26 }} aria-hidden>🔒</div>
          <p style={{ margin: "6px 0 10px", fontSize: 13, color: "var(--text-secondary)" }}>
            The full pick for this match is premium members only.
          </p>
          <Link className="btn" style={{ padding: "4px 12px", fontSize: 12.5 }} to="/app/access">
            Redeem a code to unlock
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div
      className="card match-card match-card-static tilt-card"
      ref={tilt.ref}
      onMouseMove={tilt.onMouseMove}
      onMouseLeave={tilt.onMouseLeave}
    >
      <div className="match-card-top">
        <span className="match-competition">{pick.match.league}</span>
        <span style={{ display: "flex", gap: 6, alignItems: "center" }}>
          {pick.result !== "pending" && (
            <span className={`result-tag ${pick.result}`}>{RESULT_LABELS[pick.result]}</span>
          )}
          <span className="badge-neutral">{pick.market}</span>
        </span>
      </div>

      <div className="match-teams">
        <span className="team-name">{pick.match.home_team.name}</span>
        <span className="vs-divider">vs</span>
        <span className="team-name away">{pick.match.away_team.name}</span>
      </div>

      <div className="match-meta-row">
        {new Date(pick.match.date).toLocaleString(undefined, { weekday: "short", hour: "2-digit", minute: "2-digit" })}
      </div>

      <ProbabilityBar label={pick.selection} probability={pick.probability} />

      {pick.note && (
        <p style={{ margin: "8px 0 0", fontSize: 13, color: "var(--text-secondary)", fontStyle: "italic" }}>
          &ldquo;{pick.note}&rdquo;
        </p>
      )}

      {pick.result === "unresolved" && onSetResult && (
        <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
          <button
            type="button"
            className="btn ghost"
            style={{ padding: "2px 8px", fontSize: 12 }}
            disabled={busy}
            onClick={() => onSetResult(pick.id, "won")}
            title="This pick's market can't be auto-graded -- confirm the result by hand."
          >
            Mark won
          </button>
          <button
            type="button"
            className="btn ghost"
            style={{ padding: "2px 8px", fontSize: 12 }}
            disabled={busy}
            onClick={() => onSetResult(pick.id, "lost")}
          >
            Mark lost
          </button>
        </div>
      )}
    </div>
  );
}
