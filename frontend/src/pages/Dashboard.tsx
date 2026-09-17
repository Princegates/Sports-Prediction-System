import { useEffect, useState } from "react";
import { fetchTodaysPredictions, fetchMatch } from "../api";
import { MatchCard } from "../components/MatchCard";
import type { MatchSummary, Prediction } from "../types";

interface Row {
  prediction: Prediction;
  match: MatchSummary;
}

export function Dashboard({ league }: { league: string }) {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setRows(null);
    setError(null);

    fetchTodaysPredictions(league)
      .then(async (predictions) => {
        const matches = await Promise.all(predictions.map((p) => fetchMatch(p.match_id)));
        if (!cancelled) {
          setRows(predictions.map((prediction, i) => ({ prediction, match: matches[i] })));
        }
      })
      .catch((err) => !cancelled && setError(String(err)));

    return () => {
      cancelled = true;
    };
  }, [league]);

  if (error) return <div className="empty-state">Couldn't reach the API: {error}</div>;
  if (rows === null) return <div className="loading">Loading today's matches…</div>;
  if (rows.length === 0) {
    return (
      <div className="empty-state">
        No matches scheduled for today in {league}. Run{" "}
        <code>scripts/build_predictions.py --league "{league}"</code> to pull upcoming fixtures.
      </div>
    );
  }

  return (
    <div className="grid">
      {rows.map(({ prediction, match }) => (
        <MatchCard
          key={prediction.match_id}
          prediction={prediction}
          homeTeam={match.home_team.name}
          awayTeam={match.away_team.name}
          kickoff={match.date}
        />
      ))}
    </div>
  );
}
