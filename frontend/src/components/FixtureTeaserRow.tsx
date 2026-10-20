import { Link } from "react-router-dom";
import { fixtureSlug } from "../lib/slug";
import type { PublicFixture } from "../types";
import { TeamCrest } from "./TeamCrest";

function kickoffLabel(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false });
}

/** One upcoming fixture on the public pages: crests, teams, league and
 * kickoff, and the confidence rating -- never the pick itself. Links to
 * the fixture's own page. */
export function FixtureTeaserRow({ fixture, showLeague = true }: { fixture: PublicFixture; showLeague?: boolean }) {
  return (
    <Link to={`/predict/${fixtureSlug(fixture)}`} className="fixture-teaser fixture-teaser-link">
      <div className="fixture-teaser-main">
        <span className="fixture-teaser-teams">
          <span className="crest-name">
            <TeamCrest name={fixture.home_team} logoUrl={fixture.home_logo_url} size={18} />
            {fixture.home_team}
          </span>
          <span className="vs-divider">v</span>
          <span className="crest-name">
            <TeamCrest name={fixture.away_team} logoUrl={fixture.away_logo_url} size={18} />
            {fixture.away_team}
          </span>
        </span>
        <span className="fixture-teaser-meta">
          {showLeague && <>{fixture.league} · </>}
          {kickoffLabel(fixture.kickoff)}
        </span>
      </div>
      {fixture.has_prediction ? (
        <span className={`confidence-tag ${(fixture.confidence ?? "").toLowerCase()}`}>{fixture.confidence} confidence</span>
      ) : (
        <span className="badge-neutral">Awaiting analysis</span>
      )}
    </Link>
  );
}
