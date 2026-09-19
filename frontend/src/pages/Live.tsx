import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { fetchLive, fetchMatches, fetchPrediction } from "../api";
import { leagueLabel, useLeague } from "../components/AppShell";
import { CardGridSkeleton } from "../components/LoadingSkeleton";
import { EmptyState } from "../components/EmptyState";
import { ErrorState } from "../components/ErrorState";
import { PredictionCard } from "../components/PredictionCard";
import type { MatchSummary, Prediction } from "../types";

interface Row {
  prediction: Prediction;
  match: MatchSummary;
  minute: number;
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
        const [predictions, liveHistories] = await Promise.all([
          Promise.all(live.map((m) => fetchPrediction(m.id))),
          Promise.all(live.map((m) => fetchLive(m.id))),
        ]);
        if (cancelled) return;
        setLiveRows(
          live.map((match, i) => {
            const history = liveHistories[i];
            return {
              match,
              prediction: predictions[i],
              minute: history[history.length - 1]?.minute ?? 0,
            };
          }),
        );
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
          hint="A real match appears here automatically within a few minutes of kickoff, once its league is tracked and an API-Football key is configured -- or push a simulated event from any match's Live tab below."
        />
      )}
      {!error && liveRows !== null && liveRows.length > 0 && (
        <div className="grid">
          {liveRows.map(({ prediction, match, minute }) => (
            <PredictionCard
              key={prediction.match_id}
              prediction={prediction}
              homeTeam={match.home_team.name}
              awayTeam={match.away_team.name}
              kickoff={match.date}
              competition={match.league}
              isLive
              liveScore={{ home: match.home_score ?? 0, away: match.away_score ?? 0, minute }}
            />
          ))}
        </div>
      )}

      <div className="section-header">
        <h2>Try the live engine</h2>
        <span className="meta">Simulate events on any scheduled match to see it recalculate, whether or not it's actually live</span>
      </div>

      {upcoming === null && <p className="badge-neutral">Loading scheduled matches…</p>}
      {upcoming !== null && upcoming.length === 0 && (
        <EmptyState icon="◌" title={`No scheduled matches for ${leagueLabel(league)} to simulate.`} />
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
