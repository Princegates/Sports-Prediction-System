import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { fetchMatches, fetchPrediction } from "../api";
import { useLeague } from "../components/AppShell";
import { CardGridSkeleton } from "../components/LoadingSkeleton";
import { EmptyState } from "../components/EmptyState";
import { ErrorState } from "../components/ErrorState";
import { PredictionCard } from "../components/PredictionCard";
import type { MatchSummary, Prediction } from "../types";

interface Row {
  prediction: Prediction;
  match: MatchSummary;
}

export function Live() {
  const { league } = useLeague();
  const [liveRows, setLiveRows] = useState<Row[] | null>(null);
  const [upcoming, setUpcoming] = useState<MatchSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    let cancelled = false;
    setLiveRows(null);
    setUpcoming(null);
    setError(null);

    Promise.all([fetchMatches({ status: "LIVE" }), fetchMatches({ league, status: "SCHEDULED" })])
      .then(async ([live, scheduled]) => {
        if (cancelled) return;
        const predictions = await Promise.all(live.map((m) => fetchPrediction(m.id)));
        if (cancelled) return;
        setLiveRows(live.map((match, i) => ({ match, prediction: predictions[i] })));
        setUpcoming(scheduled.slice(0, 6));
      })
      .catch((err) => !cancelled && setError(String(err)));

    return () => {
      cancelled = true;
    };
  }, [league]);

  return (
    <div>
      <div className="section-header">
        <h2>Live Match Center</h2>
        <span className="meta">Real-time in-play probability recalculation</span>
      </div>

      {error && <ErrorState message={error} />}
      {!error && liveRows === null && <CardGridSkeleton count={2} />}
      {!error && liveRows !== null && liveRows.length === 0 && (
        <EmptyState
          icon="●"
          title="No matches are live right now."
          hint="Live status appears here the moment a match receives a live event (goal, card, substitution, etc.)."
        />
      )}
      {!error && liveRows !== null && liveRows.length > 0 && (
        <div className="grid">
          {liveRows.map(({ prediction, match }) => (
            <PredictionCard
              key={prediction.match_id}
              prediction={prediction}
              homeTeam={match.home_team.name}
              awayTeam={match.away_team.name}
              kickoff={match.date}
              competition={match.league}
              isLive
              liveScore={{ home: match.home_score ?? 0, away: match.away_score ?? 0, minute: 0 }}
            />
          ))}
        </div>
      )}

      <div className="section-header">
        <h2>Try the live engine</h2>
        <span className="meta">No paid live-data feed is wired up -- simulate events on any scheduled match instead</span>
      </div>

      {upcoming === null && <p className="badge-neutral">Loading scheduled matches…</p>}
      {upcoming !== null && upcoming.length === 0 && (
        <EmptyState icon="◌" title={`No scheduled matches for ${league} to simulate.`} />
      )}
      {upcoming !== null && upcoming.length > 0 && (
        <div className="card card-pad">
          <p style={{ marginTop: 0, color: "var(--text-secondary)", fontSize: 13.5 }}>
            Pick a scheduled match, open its <strong>Live</strong> tab, and push simulated events (goal, red card,
            substitution...) to watch the Global Most-Likely Outcome recalculate immediately.
          </p>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {upcoming.map((m) => (
              <div key={m.id} className="transparency-row" style={{ cursor: "pointer" }} onClick={() => navigate(`/app/match/${m.id}?tab=Live`)}>
                <span className="name" style={{ width: "auto", flex: 1 }}>
                  {m.home_team.name} vs {m.away_team.name}
                </span>
                <span className="pct" style={{ width: "auto" }}>
                  {new Date(m.date).toLocaleDateString(undefined, { month: "short", day: "numeric" })}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
