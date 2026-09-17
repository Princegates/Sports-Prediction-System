import { useEffect, useState } from "react";
import { fetchMatch, fetchMatches, fetchMostLikely, fetchPrediction } from "../api";
import { useLeague } from "../components/AppShell";
import { CardGridSkeleton } from "../components/LoadingSkeleton";
import { EmptyState } from "../components/EmptyState";
import { ErrorState } from "../components/ErrorState";
import { PredictionCard } from "../components/PredictionCard";
import { SearchCommand } from "../components/SearchCommand";
import { LEAGUES } from "../components/AppShell";
import { isHighConfidence, dateOffset } from "../lib/filters";
import type { MatchSummary, Prediction } from "../types";

interface Row {
  prediction: Prediction;
  match: MatchSummary;
}

type RangeFilter = "today" | "tomorrow" | "week";

const EXAMPLE_PROMPTS = [
  "Show today's highest-confidence predictions",
  "Analyze a specific matchup",
  "Which team has the stronger recent form?",
  "Why does the model favor this outcome?",
];

async function loadForDate(league: string, date: string): Promise<Row[]> {
  const matches = await fetchMatches({ league, date });
  const predictions = await Promise.all(matches.map((m) => fetchPrediction(m.id)));
  return matches.map((match, i) => ({ match, prediction: predictions[i] }));
}

async function loadForWeek(league: string): Promise<Row[]> {
  const predictions = await fetchMostLikely(league, 7, 24);
  const matches = await Promise.all(predictions.map((p) => fetchMatch(p.match_id)));
  return predictions
    .map((prediction, i) => ({ prediction, match: matches[i] }))
    .sort((a, b) => new Date(a.match.date).getTime() - new Date(b.match.date).getTime());
}

export function Dashboard() {
  const { league } = useLeague();
  const [range, setRange] = useState<RangeFilter>("today");
  const [highConfidenceOnly, setHighConfidenceOnly] = useState(false);
  const [rows, setRows] = useState<Row[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [searchOpen, setSearchOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setRows(null);
    setError(null);

    const loader =
      range === "today" ? loadForDate(league, dateOffset(0)) : range === "tomorrow" ? loadForDate(league, dateOffset(1)) : loadForWeek(league);

    loader.then((r) => !cancelled && setRows(r)).catch((err) => !cancelled && setError(String(err)));

    return () => {
      cancelled = true;
    };
  }, [league, range]);

  const visibleRows = rows ? (highConfidenceOnly ? rows.filter((r) => isHighConfidence(r.prediction)) : rows) : null;

  return (
    <div>
      <section className="hero">
        <div className="hero-eyebrow">
          <span className="dot" /> AI Football Intelligence
        </div>
        <h1>Every match, every angle, one probability.</h1>
        <p>Analyze matches, discover patterns and understand what the data says -- with the reasoning always shown alongside the number.</p>

        <div className="ai-search" onClick={() => setSearchOpen(true)}>
          <span className="icon">◈</span>
          <span className="placeholder">Search for a team to see its full AI profile...</span>
        </div>

        <div className="example-prompts">
          {EXAMPLE_PROMPTS.map((prompt) => (
            <button
              key={prompt}
              className="prompt-chip"
              onClick={() => {
                if (prompt.startsWith("Show today's")) {
                  setRange("today");
                  setHighConfidenceOnly(true);
                } else {
                  setSearchOpen(true);
                }
              }}
            >
              {prompt}
            </button>
          ))}
        </div>
      </section>

      <div className="section-header">
        <h2>Match discovery</h2>
        <div className="filter-bar">
          <button className={`filter-chip${range === "today" ? " active" : ""}`} onClick={() => setRange("today")}>
            Today
          </button>
          <button className={`filter-chip${range === "tomorrow" ? " active" : ""}`} onClick={() => setRange("tomorrow")}>
            Tomorrow
          </button>
          <button className={`filter-chip${range === "week" ? " active" : ""}`} onClick={() => setRange("week")}>
            This Week
          </button>
          <button className={`filter-chip${highConfidenceOnly ? " active" : ""}`} onClick={() => setHighConfidenceOnly((v) => !v)}>
            High confidence only
          </button>
        </div>
      </div>

      {error && <ErrorState message={error} />}
      {!error && visibleRows === null && <CardGridSkeleton />}
      {!error && visibleRows !== null && visibleRows.length === 0 && (
        <EmptyState
          icon="◌"
          title={`No ${highConfidenceOnly ? "high-confidence " : ""}matches found for ${league} in this range.`}
          hint={
            <>
              Import more fixtures with <code>scripts/fetch_openfootball_data.py</code> or widen the date range.
            </>
          }
        />
      )}
      {!error && visibleRows !== null && visibleRows.length > 0 && (
        <div className="grid">
          {visibleRows.map(({ prediction, match }) => (
            <PredictionCard
              key={prediction.match_id}
              prediction={prediction}
              homeTeam={match.home_team.name}
              awayTeam={match.away_team.name}
              kickoff={match.date}
              competition={match.league}
              isLive={match.status === "LIVE"}
              liveScore={match.status === "LIVE" ? { home: match.home_score ?? 0, away: match.away_score ?? 0, minute: 0 } : undefined}
            />
          ))}
        </div>
      )}

      <SearchCommand open={searchOpen} onClose={() => setSearchOpen(false)} leagues={LEAGUES} />
    </div>
  );
}
