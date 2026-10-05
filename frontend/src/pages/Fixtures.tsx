import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { fetchPublicFixtures } from "../api";
import { PublicShell } from "../components/PublicShell";
import { fixtureSlug } from "../lib/slug";
import type { PublicFixture } from "../types";

/**
 * Public hub page listing every upcoming fixture, each linking to its own
 * preview page (FixturePreview.tsx). Two jobs: a real page for someone
 * browsing rather than searching one specific matchup, and -- just as
 * important for search -- a page search engines can crawl to actually
 * discover the individual fixture pages, which otherwise have no other
 * page linking to more than a handful of them (Welcome.tsx's teaser only
 * shows a handful of the soonest ones).
 */
export function Fixtures() {
  const [fixtures, setFixtures] = useState<PublicFixture[] | null>(null);

  useEffect(() => {
    fetchPublicFixtures(14, 20)
      .then(setFixtures)
      .catch(() => setFixtures([]));
  }, []);

  return (
    <PublicShell
      title="Upcoming Football Fixtures & AI Predictions — Socca Intelligence"
      description="Every upcoming fixture this system is tracking, across the Premier League, La Liga, Serie A, Bundesliga, Ligue 1 and more -- with each match's own prediction-confidence page."
    >
      <section className="landing-section">
        <div className="section-head">
          <h2>Upcoming fixtures</h2>
          <p>
            Real fixtures from the database, over the next two weeks. Open any match for its own page --
            the full breakdown (selection, probability, reasoning) stays behind the login, same as everywhere
            else on this site.
          </p>
        </div>

        {fixtures === null && <p className="meta">Loading fixtures…</p>}

        {fixtures !== null && fixtures.length === 0 && (
          <p className="meta">No upcoming fixtures are loaded right now -- check back soon.</p>
        )}

        {fixtures !== null && fixtures.length > 0 && (
          <div className="fixture-teaser-list">
            {fixtures.map((f) => (
              <Link key={f.match_id} to={`/predict/${fixtureSlug(f)}`} className="fixture-teaser fixture-teaser-link">
                <div className="fixture-teaser-main">
                  <span className="fixture-teaser-teams">
                    {f.home_team} <span className="vs-divider">v</span> {f.away_team}
                  </span>
                  <span className="fixture-teaser-meta">
                    {f.league} ·{" "}
                    {new Date(f.kickoff).toLocaleString(undefined, {
                      weekday: "short",
                      day: "numeric",
                      month: "short",
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </span>
                </div>
                {f.has_prediction ? (
                  <span className={`confidence-tag ${(f.confidence ?? "").toLowerCase()}`}>
                    {f.confidence} confidence
                  </span>
                ) : (
                  <span className="badge-neutral">Awaiting analysis</span>
                )}
              </Link>
            ))}
          </div>
        )}
      </section>
    </PublicShell>
  );
}
