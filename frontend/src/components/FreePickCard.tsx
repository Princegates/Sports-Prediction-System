import type { FreePick } from "../types";
import { useTilt } from "../lib/useTilt";
import { ConfidenceTag } from "./MostLikelyOutcome";
import { ProbabilityBar } from "./ProbabilityBar";

/**
 * The free tier's card -- deliberately not clickable. The match behind it is
 * still premium (MatchDetail sits behind RequireAccess), so sending someone
 * there just to bounce them back to /app/access would be a dead end dressed
 * up as a link. The "redeem a code" prompt lives once, above the whole grid,
 * instead of repeated as a lie on every card.
 */
export function FreePickCard({ pick }: { pick: FreePick }) {
  const tilt = useTilt<HTMLDivElement>();

  return (
    <div className="card match-card tilt-card" ref={tilt.ref} onMouseMove={tilt.onMouseMove} onMouseLeave={tilt.onMouseLeave}>
      <div className="match-card-top">
        <span className="match-competition">{pick.league}</span>
        <ConfidenceTag confidence={pick.confidence} />
      </div>

      <div className="match-teams">
        <span className="team-name">{pick.home_team}</span>
        <span className="vs-divider">vs</span>
        <span className="team-name away">{pick.away_team}</span>
      </div>

      <div className="match-meta-row">
        {new Date(pick.kickoff).toLocaleString(undefined, { weekday: "short", hour: "2-digit", minute: "2-digit" })}
      </div>

      <div>
        <ProbabilityBar label="Home" probability={pick.home_win} variant="home" />
        <ProbabilityBar label="Draw" probability={pick.draw} variant="draw" />
        <ProbabilityBar label="Away" probability={pick.away_win} variant="away" />
      </div>

      <div className="match-meta-row" style={{ marginTop: 8 }}>
        <strong>{pick.selection}</strong>
        <span className="tabular-nums" style={{ marginLeft: 6 }}>
          {(pick.probability * 100).toFixed(0)}%
        </span>
      </div>
    </div>
  );
}
