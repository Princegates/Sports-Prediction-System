import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { fetchPublicFixture } from "../api";
import { Mascot } from "../components/Mascot";
import { PublicShell } from "../components/PublicShell";
import { usePageStructuredData } from "../lib/pageMeta";
import { chanceLabel } from "../lib/odds";
import { fixtureSlug, leagueSlug, parseFixtureId } from "../lib/slug";
import { TeamCrest } from "../components/TeamCrest";
import type { PublicFixture, PublicFixtureDetail, PublicResult } from "../types";

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
  | { kind: "ready"; fixture: PublicFixtureDetail };

function formatKickoff(iso: string): { date: string; time: string } {
  const d = new Date(iso);
  return {
    date: d.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long", year: "numeric" }),
    time: d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", hour12: false }),
  };
}

function fixtureDescription(fixture: PublicFixture): string {
  const matchup = `${fixture.home_team} vs ${fixture.away_team}`;
  const { date } = formatKickoff(fixture.kickoff);
  return fixture.has_prediction
    ? `${matchup} prediction: ${fixture.league}, ${date}. Rated ${(fixture.confidence ?? "").toLowerCase()} confidence by our model, with both sides' recent form and past meetings.`
    : `${matchup}: ${fixture.league}, ${date}. Recent form, past meetings, and a full prediction closer to kickoff.`;
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
  const url = `https://soccaintel.com/predict/${fixtureSlug(fixture)}`;
  return {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "SportsEvent",
        name: `${fixture.home_team} vs ${fixture.away_team}`,
        startDate: fixture.kickoff,
        sport: "Football",
        eventStatus: "https://schema.org/EventScheduled",
        homeTeam: { "@type": "SportsTeam", name: fixture.home_team },
        awayTeam: { "@type": "SportsTeam", name: fixture.away_team },
        description: fixtureDescription(fixture),
        url,
      },
      {
        "@type": "BreadcrumbList",
        itemListElement: [
          { "@type": "ListItem", position: 1, name: "Home", item: "https://soccaintel.com/" },
          { "@type": "ListItem", position: 2, name: "Fixtures", item: "https://soccaintel.com/fixtures" },
          {
            "@type": "ListItem",
            position: 3,
            name: `${fixture.league} predictions`,
            item: `https://soccaintel.com/predictions/${leagueSlug(fixture.league)}`,
          },
          { "@type": "ListItem", position: 4, name: `${fixture.home_team} vs ${fixture.away_team}`, item: url },
        ],
      },
    ],
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

function FormColumn({ team, logoUrl, results }: { team: string; logoUrl?: string | null; results: PublicResult[] }) {
  return (
    <div className="card card-pad form-column">
      <h3 className="crest-name" style={{ margin: 0, fontSize: 15 }}>
        <TeamCrest name={team} logoUrl={logoUrl} size={22} />
        {team}
      </h3>
      <div className="form-chips" aria-label={`Last results: ${results.map((r) => r.result).join(" ")}`}>
        {results.map((r, i) => (
          <span key={i} className={`form-chip ${r.result.toLowerCase()}`}>
            {r.result}
          </span>
        ))}
      </div>
      {results.length === 0 ? (
        <p className="meta" style={{ margin: 0 }}>No recent results on record.</p>
      ) : (
        <ul className="form-results">
          {results.map((r, i) => (
            <li key={i}>
              <span className="meta">{r.venue === "H" ? "vs" : "at"}</span> {r.opponent}{" "}
              <strong>
                {r.goals_for}–{r.goals_against}
              </strong>
            </li>
          ))}
        </ul>
      )}
    </div>
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
  const pick = fixture.pick_of_the_day;

  return (
    <PublicShell title={pageTitle} description={pageDescription}>
      <nav className="breadcrumbs" aria-label="Breadcrumb">
        <Link to="/">Home</Link> › <Link to="/fixtures">Fixtures</Link> ›{" "}
        <Link to={`/predictions/${leagueSlug(fixture.league)}`}>{fixture.league}</Link>
      </nav>

      <section className="landing-section">
        <div className="section-head">
          <h1 className="fixture-title">
            <span className="crest-name">
              <TeamCrest name={fixture.home_team} logoUrl={fixture.home_logo_url} size={36} />
              {fixture.home_team}
            </span>{" "}
            <span className="vs-divider">vs</span>{" "}
            <span className="crest-name">
              <TeamCrest name={fixture.away_team} logoUrl={fixture.away_logo_url} size={36} />
              {fixture.away_team}
            </span>
          </h1>
          <p>
            {fixture.league} · {date} at {time} (your local time)
          </p>
        </div>

        <div className="card card-pad" style={{ maxWidth: 620 }}>
          {pick ? (
            <>
              <span className="eyebrow">Today's free pick</span>
              <div className="potd-call" style={{ marginTop: "var(--space-3)" }}>
                <span className="potd-selection">
                  {pick.market}: <strong>{pick.selection}</strong>
                </span>
                <span className="potd-prob">{Math.round((pick.probability ?? 0) * 100)}%</span>
              </div>
              <p style={{ marginTop: "var(--space-3)" }}>
                This is the model's strongest call kicking off soon, so it's free for everyone today. By its own numbers
                it lands {chanceLabel(pick.probability ?? 0)} of the time, so it can still lose. Members see a pick like
                this, with the reasoning, for every match.
              </p>
            </>
          ) : fixture.has_prediction ? (
            <>
              <span className={`confidence-tag ${(fixture.confidence ?? "").toLowerCase()}`}>{fixture.confidence} confidence</span>
              <p style={{ marginTop: "var(--space-3)" }}>
                The model has rated this match <strong>{(fixture.confidence ?? "").toLowerCase()} confidence</strong>.
                Members see its pick, the probability behind it, and the factors pushing it either way, across 100+
                markets from the match result to the correct score.
              </p>
            </>
          ) : (
            <>
              <span className="badge-neutral">Awaiting analysis</span>
              <p style={{ marginTop: "var(--space-3)" }}>
                The prediction for this match is published closer to kickoff. The recent form and past meetings below
                are already in.
              </p>
            </>
          )}
          <div className="status-result-actions" style={{ marginTop: "var(--space-4)" }}>
            <Link className="btn btn-lg" to="/register">
              {pick ? "Get every match's pick free" : "See the prediction free"}
            </Link>
          </div>
        </div>
      </section>

      {(fixture.home_form.length > 0 || fixture.away_form.length > 0) && (
        <section className="landing-section">
          <div className="section-head">
            <h2>Recent form</h2>
            <p>Each side's last {Math.max(fixture.home_form.length, fixture.away_form.length)} results before this match.</p>
          </div>
          <div className="form-columns">
            <FormColumn team={fixture.home_team} logoUrl={fixture.home_logo_url} results={fixture.home_form} />
            <FormColumn team={fixture.away_team} logoUrl={fixture.away_logo_url} results={fixture.away_form} />
          </div>
        </section>
      )}

      {fixture.head_to_head.length > 0 && (
        <section className="landing-section">
          <div className="section-head">
            <h2>Head to head</h2>
            <p>
              {fixture.head_to_head.length === 1
                ? "The last time these sides met."
                : `The last ${fixture.head_to_head.length} times these sides met.`}
            </p>
          </div>
          <div className="card card-pad h2h-list">
            {fixture.head_to_head.map((m) => (
              <div key={`${m.date}-${m.home_team}`} className="h2h-row">
                <span className="meta">{new Date(m.date).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })}</span>
                <span className="h2h-teams">
                  {m.home_team} <strong className="h2h-score">{m.home_score} – {m.away_score}</strong> {m.away_team}
                </span>
                <span className="meta">{m.league}</span>
              </div>
            ))}
          </div>
        </section>
      )}

      <section className="landing-section">
        <div className="section-head">
          <h2>More fixtures</h2>
        </div>
        <div className="landing-cta-row">
          <Link className="btn ghost" to={`/predictions/${leagueSlug(fixture.league)}`}>
            More {fixture.league} predictions
          </Link>
          <Link className="btn ghost" to="/fixtures">
            All upcoming fixtures
          </Link>
        </div>
      </section>
    </PublicShell>
  );
}
