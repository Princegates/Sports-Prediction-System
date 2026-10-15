import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { fetchPublicFixture } from "../api";
import { Mascot } from "../components/Mascot";
import { PublicShell } from "../components/PublicShell";
import { usePageStructuredData } from "../lib/pageMeta";
import { fixtureSlug, parseFixtureId } from "../lib/slug";
import type { PublicFixture } from "../types";

/**
 * Public, no-login preview page for one upcoming fixture -- the page
 * someone searching "<team> vs <team> prediction" actually lands on,
 * instead of nowhere. Real match content (so it's worth indexing, not thin
 * duplicate boilerplate), but it follows the same rule as every other
 * public endpoint: it may say a prediction exists and how confident it is,
 * never the selection or the probability. Those stay the product, behind
 * the login -- this page's whole job is to convert the search visitor into
 * someone who signs up to see them.
 */

type State =
  | { kind: "loading" }
  | { kind: "not-found" }
  | { kind: "ready"; fixture: PublicFixture };

function formatKickoff(iso: string): { date: string; time: string } {
  const d = new Date(iso);
  return {
    date: d.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long", year: "numeric" }),
    time: d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }),
  };
}

function fixtureDescription(fixture: PublicFixture): string {
  const matchup = `${fixture.home_team} vs ${fixture.away_team}`;
  const { date } = formatKickoff(fixture.kickoff);
  return fixture.has_prediction
    ? `${matchup} kicks off ${date} in the ${fixture.league}. Our model has analyzed this fixture with ${(fixture.confidence ?? "").toLowerCase()} confidence -- see the full prediction across 18 markets.`
    : `${matchup} kicks off ${date} in the ${fixture.league}. Full AI-driven prediction coverage across 18 markets, powered by an Elo/Poisson/gradient-boosting ensemble.`;
}

// SportsEvent structured data, built only from fields this endpoint
// actually returns -- no venue/location (not in PublicFixture, so not
// guessed). Verified directly against this site's CSP (script-src 'self',
// no 'unsafe-inline') that an inline application/ld+json script is NOT
// blocked -- Chrome only governs script-src for elements it treats as
// executable script, and this type attribute isn't one (see
// usePageStructuredData's own comment). eventStatus is always "scheduled":
// this endpoint only ever returns fixtures that haven't kicked off yet
// (see NotFound's copy below).
function fixtureStructuredData(fixture: PublicFixture): Record<string, unknown> {
  return {
    "@context": "https://schema.org",
    "@type": "SportsEvent",
    name: `${fixture.home_team} vs ${fixture.away_team}`,
    startDate: fixture.kickoff,
    sport: "Football",
    eventStatus: "https://schema.org/EventScheduled",
    homeTeam: { "@type": "SportsTeam", name: fixture.home_team },
    awayTeam: { "@type": "SportsTeam", name: fixture.away_team },
    description: fixtureDescription(fixture),
    url: `https://soccaintel.com/predict/${fixtureSlug(fixture)}`,
  };
}

function NotFound() {
  return (
    <PublicShell
      title="Fixture not found — Socca Intelligence"
      description="That fixture isn't one this system is currently tracking -- see the full list of upcoming fixtures instead."
    >
      <section className="status-result-shell" style={{ padding: "var(--space-5) 0" }}>
        <div className="card card-pad status-result pending" style={{ maxWidth: 480, width: "100%" }}>
          <Mascot pose="thinking" size={80} />
          <h1>That fixture isn't live right now</h1>
          <p className="status-result-message">
            It may have already kicked off, be too far out to preview yet, or the link may be wrong.
          </p>
          <div className="status-result-actions">
            <Link className="btn btn-lg" to="/fixtures">
              See upcoming fixtures
            </Link>
          </div>
        </div>
      </section>
    </PublicShell>
  );
}

export function FixturePreview() {
  const { slugId } = useParams<{ slugId: string }>();
  const matchId = parseFixtureId(slugId);
  const [state, setState] = useState<State>({ kind: "loading" });

  useEffect(() => {
    if (matchId === null) {
      setState({ kind: "not-found" });
      return;
    }
    setState({ kind: "loading" });
    fetchPublicFixture(matchId)
      .then((fixture) => setState({ kind: "ready", fixture }))
      .catch(() => setState({ kind: "not-found" }));
  }, [matchId]);

  // Built unconditionally, before any early return below -- React calls
  // hooks in the same order every render, so usePageStructuredData can't
  // be called only on the "ready" branch once state later moves from
  // "loading" to "ready" within the same mounted component.
  const readyFixture = state.kind === "ready" ? state.fixture : null;
  usePageStructuredData(readyFixture ? fixtureStructuredData(readyFixture) : null);

  if (state.kind === "not-found") return <NotFound />;

  if (state.kind === "loading") {
    return (
      <PublicShell title="Loading fixture… — Socca Intelligence" description="Loading this fixture's preview.">
        <section className="status-result-shell" style={{ padding: "var(--space-5) 0" }}>
          <div className="card card-pad status-result" style={{ maxWidth: 480, width: "100%" }}>
            <Mascot pose="thinking" size={80} />
            <p className="status-result-message">Loading…</p>
          </div>
        </section>
      </PublicShell>
    );
  }

  const { fixture } = state;
  const { date, time } = formatKickoff(fixture.kickoff);
  const matchup = `${fixture.home_team} vs ${fixture.away_team}`;
  const pageTitle = `${matchup} Prediction — ${fixture.league} | Socca Intelligence`;
  const pageDescription = fixtureDescription(fixture);

  return (
    <PublicShell title={pageTitle} description={pageDescription}>
      <section className="landing-section">
        <div className="section-head">
          <p className="meta">{fixture.league}</p>
          <h1>{matchup}</h1>
          <p>
            Kicks off {date} at {time} (your local time).
          </p>
        </div>

        <div className="card card-pad" style={{ maxWidth: 560 }}>
          {fixture.has_prediction ? (
            <>
              <span className={`confidence-tag ${(fixture.confidence ?? "").toLowerCase()}`}>
                {fixture.confidence} confidence
              </span>
              <p style={{ marginTop: "var(--space-3)" }}>
                Our ensemble -- Elo ratings, a Dixon-Coles Poisson goal model and a gradient-boosting
                classifier, blended and calibrated against held-out history -- has analyzed this fixture
                across 18 markets: match result, double chance, both-teams-to-score, over/under lines and
                the full correct-score grid.
              </p>
            </>
          ) : (
            <>
              <span className="badge-neutral">Awaiting analysis</span>
              <p style={{ marginTop: "var(--space-3)" }}>
                This fixture is in the database and will be analyzed closer to kickoff, the same way every
                other fixture on this system is.
              </p>
            </>
          )}
          <p className="fixture-teaser-foot" style={{ marginTop: "var(--space-3)" }}>
            The selection, the probability and the reasoning behind it stay behind the login -- that's the
            product, not the preview.
          </p>
          <div className="status-result-actions" style={{ marginTop: "var(--space-4)" }}>
            <Link className="btn btn-lg" to="/register">
              Sign up free to see the full prediction
            </Link>
          </div>
        </div>
      </section>

      <section className="landing-section">
        <div className="section-head">
          <h2>More fixtures</h2>
        </div>
        <Link className="btn ghost" to="/fixtures">
          See all upcoming fixtures
        </Link>
      </section>
    </PublicShell>
  );
}
