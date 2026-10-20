import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { fetchPublicFixtures, fetchPublicStats } from "../api";
import { FixtureTeaserRow } from "../components/FixtureTeaserRow";
import { PublicShell } from "../components/PublicShell";
import { leagueSlug } from "../lib/slug";
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
  const [leagues, setLeagues] = useState<string[]>([]);

  useEffect(() => {
    fetchPublicFixtures(14, 20)
      .then(setFixtures)
      .catch(() => setFixtures([]));
    fetchPublicStats()
      .then((s) => setLeagues(s.league_names))
      .catch(() => setLeagues([]));
  }, []);

  return (
    <PublicShell
      title="Today's Football & Soccer Predictions — Upcoming Fixtures | Socca Intelligence"
      description="Upcoming football and soccer fixtures with AI prediction-confidence previews -- Premier League, La Liga, Serie A, Bundesliga, Ligue 1 and 50+ more leagues, each with its own match page."
    >
      <section className="landing-section">
        <div className="section-head">
          {/* The page's only H1 -- it was an H2 with no H1 above it, which
              both skips a real on-page SEO signal and breaks the one-H1-
              per-page structure every other public page here follows. */}
          <h1>Upcoming Football &amp; Soccer Predictions</h1>
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
              <FixtureTeaserRow key={f.match_id} fixture={f} />
            ))}
          </div>
        )}
      </section>

      {leagues.length > 0 && (
        <section className="landing-section">
          <div className="section-head">
            <h2>Predictions by league</h2>
            <p>Every upcoming fixture in one competition, on its own page.</p>
          </div>
          <div className="league-links">
            {leagues.map((l) => (
              <Link key={l} to={`/predictions/${leagueSlug(l)}`} className="filter-chip">
                {l}
              </Link>
            ))}
          </div>
        </section>
      )}
    </PublicShell>
  );
}
