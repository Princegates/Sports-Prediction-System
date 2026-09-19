import type { FeaturedPick } from "../types";
import { useTilt } from "../lib/useTilt";
import { ProbabilityBar } from "./ProbabilityBar";

/** Not clickable, same reasoning as FreePickCard -- the match behind it may
 * still be premium, and the card's own probability is what's being shown
 * off here, not a doorway into the full breakdown. */
export function GudaPickCard({ pick }: { pick: FeaturedPick }) {
  const tilt = useTilt<HTMLDivElement>();

  return (
    <div className="card match-card tilt-card" ref={tilt.ref} onMouseMove={tilt.onMouseMove} onMouseLeave={tilt.onMouseLeave}>
      <div className="match-card-top">
        <span className="match-competition">{pick.match.league}</span>
        <span className="badge-neutral">{pick.market}</span>
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
    </div>
  );
}
