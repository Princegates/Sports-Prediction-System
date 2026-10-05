import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { fetchPublicFixture } from "../api";
import { Mascot } from "../components/Mascot";
import { PublicShell } from "../components/PublicShell";
import { parseFixtureId } from "../lib/slug";
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
  const pageDescription = fixture.has_prediction
    ? `${matchup} kicks off ${date} in the ${fixture.league}. Our model has analyzed this fixture with ${(fixture.confidence ?? "").toLowerCase()} confidence -- see the full prediction across 18 markets.`
    : `${matchup} kicks off ${date} in the ${fixture.league}. Full AI-driven prediction coverage across 18 markets, powered by an Elo/Poisson/gradient-boosting ensemble.`;

  // No JSON-LD structured-data block here on purpose: it would need an
  // inline <script> tag, and this site's CSP deliberately locks script-src
  // to 'self' with no 'unsafe-inline' exception (see public/_headers) --
  // added there as a considered security tradeoff, not an oversight, so
  // this page doesn't carve a hole in it for a minor rich-result nicety.
  // Title, description and real visible content below do the actual
  // indexing/ranking work.

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
