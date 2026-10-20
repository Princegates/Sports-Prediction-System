import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { fetchLeagueFixtures, fetchPublicStats } from "../api";
import { FixtureTeaserRow } from "../components/FixtureTeaserRow";
import { PublicShell } from "../components/PublicShell";
import { usePageStructuredData } from "../lib/pageMeta";
import { leagueSlug } from "../lib/slug";
import type { PublicFixture } from "../types";

/** A league's hub page -- /predictions/english-premier-league and so on.
 * Searches like "premier league predictions today" are among the biggest in
 * this market, and before this every fixture page hung off one flat list.
 * Lists the league's upcoming fixtures (confidence rating only, like every
 * public list) and links to each fixture's own page. */
export function LeaguePredictions() {
  const { leagueSlug: slug } = useParams<{ leagueSlug: string }>();
  const [league, setLeague] = useState<string | null | undefined>(undefined);
  const [fixtures, setFixtures] = useState<PublicFixture[] | null>(null);

  useEffect(() => {
    setLeague(undefined);
    setFixtures(null);
    fetchPublicStats()
      .then((stats) => {
        const name = stats.league_names.find((l) => leagueSlug(l) === slug) ?? null;
        setLeague(name);
        if (name) fetchLeagueFixtures(name, 14).then(setFixtures).catch(() => setFixtures([]));
      })
      .catch(() => setLeague(null));
  }, [slug]);

  const name = league ?? "League";
  usePageStructuredData(
    league
      ? {
          "@context": "https://schema.org",
          "@type": "BreadcrumbList",
          itemListElement: [
            { "@type": "ListItem", position: 1, name: "Home", item: "https://soccaintel.com/" },
            { "@type": "ListItem", position: 2, name: "Fixtures", item: "https://soccaintel.com/fixtures" },
            { "@type": "ListItem", position: 3, name: `${league} predictions`, item: `https://soccaintel.com/predictions/${slug}` },
          ],
        }
      : null,
  );

  if (league === null) {
    return (
      <PublicShell title="League not found — Socca Intelligence" description="That league isn't one we cover.">
        <section className="landing-section">
          <div className="section-head">
            <h1>We don't cover that league</h1>
            <p>
              <Link to="/fixtures">See every league and fixture we do cover</Link>.
            </p>
          </div>
        </section>
      </PublicShell>
    );
  }

  return (
    <PublicShell
      title={`${name} Predictions Today — Upcoming Fixtures | Socca Intelligence`}
      description={`${name} predictions for the next two weeks: every upcoming fixture rated for confidence by our model, with recent form and past meetings on each match page.`}
    >
      <nav className="breadcrumbs" aria-label="Breadcrumb">
        <Link to="/">Home</Link> › <Link to="/fixtures">Fixtures</Link> › {name}
      </nav>
      <section className="landing-section">
        <div className="section-head">
          <h1>{name} predictions</h1>
          <p>
            Every upcoming {name} fixture over the next two weeks, rated for confidence by our model. Open a match for
            both sides' recent form and their past meetings; the pick itself and its probability are for members.
          </p>
        </div>
        {fixtures === null && <p className="meta">Loading fixtures…</p>}
        {fixtures !== null && fixtures.length === 0 && (
          <p className="meta">No {name} fixtures in the next two weeks -- check back soon.</p>
        )}
        {fixtures !== null && fixtures.length > 0 && (
          <div className="fixture-teaser-list">
            {fixtures.map((f) => (
              <FixtureTeaserRow key={f.match_id} fixture={f} showLeague={false} />
            ))}
          </div>
        )}
      </section>
      <section className="doc-cta">
        <h2>See the picks, not just the ratings</h2>
        <p>Start with a free trial -- no card, no code.</p>
        <div className="landing-cta-row">
          <Link className="btn btn-lg" to="/register">
            Start free trial
          </Link>
        </div>
      </section>
    </PublicShell>
  );
}
