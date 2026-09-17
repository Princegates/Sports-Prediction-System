import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { fetchLive, fetchMatch, fetchPrediction, fetchStatistics, type TeamForm } from "../api";
import { ConfidenceTag, MostLikelyBadge } from "../components/MostLikelyBadge";
import { ProbabilityBar } from "../components/ProbabilityBar";
import type { LivePrediction, MatchSummary, Prediction } from "../types";

function ScoreGrid({ scores }: { scores: Record<string, number> }) {
  const entries = Object.entries(scores).sort((a, b) => b[1] - a[1]);
  const max = Math.max(...entries.map(([, p]) => p));
  return (
    <div className="score-grid-wrapper">
      <div className="score-grid" style={{ gridTemplateColumns: `repeat(${Math.min(entries.length, 4)}, 1fr)` }}>
        {entries.map(([score, p]) => (
          <div
            key={score}
            className="score-cell"
            style={{ opacity: 0.35 + 0.65 * (p / max) }}
            title={`${(p * 100).toFixed(1)}%`}
          >
            {score}
            <br />
            {(p * 100).toFixed(1)}%
          </div>
        ))}
      </div>
    </div>
  );
}

function FormSummary({ label, form }: { label: string; form: TeamForm }) {
  return (
    <div>
      <div className="match-meta">{label}</div>
      <div style={{ fontSize: "0.85rem", lineHeight: 1.6 }}>
        Form: {form.recent_results.join(" ") || "n/a"}
        <br />
        {form.points_per_game.toFixed(2)} pts/game &middot; {form.goals_scored_avg.toFixed(2)} scored /{" "}
        {form.goals_conceded_avg.toFixed(2)} conceded per game
        <br />
        Clean sheets: {(form.clean_sheet_rate * 100).toFixed(0)}% &middot; Rest: {form.rest_days.toFixed(0)}d
      </div>
    </div>
  );
}

export function MatchDetail() {
  const { id } = useParams();
  const matchId = Number(id);

  const [match, setMatch] = useState<MatchSummary | null>(null);
  const [prediction, setPrediction] = useState<Prediction | null>(null);
  const [stats, setStats] = useState<{ home_form: TeamForm; away_form: TeamForm } | null>(null);
  const [live, setLive] = useState<LivePrediction[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([fetchMatch(matchId), fetchPrediction(matchId), fetchStatistics(matchId), fetchLive(matchId)])
      .then(([m, p, s, l]) => {
        if (cancelled) return;
        setMatch(m);
        setPrediction(p);
        setStats(s);
        setLive(l);
      })
      .catch((err) => !cancelled && setError(String(err)));
    return () => {
      cancelled = true;
    };
  }, [matchId]);

  if (error) return <div className="empty-state">Couldn't load this match: {error}</div>;
  if (!match || !prediction || !stats) return <div className="loading">Loading match…</div>;

  const latestLive = live.length > 0 ? live[live.length - 1] : null;

  return (
    <div>
      <Link className="back-link" to="/">
        ← Back to today's matches
      </Link>

      <div className="card">
        <div className="match-title" style={{ fontSize: "1.2rem" }}>
          {match.home_team.name} vs {match.away_team.name}
        </div>
        <div className="match-meta">
          {new Date(match.date).toLocaleString(undefined, { dateStyle: "full", timeStyle: "short" })} &middot;{" "}
          {match.league} &middot; <ConfidenceTag confidence={prediction.confidence} />
        </div>

        {latestLive && (
          <div className="badge medium" style={{ marginBottom: 12 }}>
            LIVE {latestLive.minute}' &middot; {latestLive.score_home}-{latestLive.score_away} &middot; updated on{" "}
            {latestLive.trigger_event.replace(/_/g, " ")}
          </div>
        )}

        <div className="two-col">
          <div>
            <div className="section-title">Match result</div>
            <ProbabilityBar label="Home" probability={latestLive?.home_win ?? prediction.home_win} />
            <ProbabilityBar label="Draw" probability={latestLive?.draw ?? prediction.draw} />
            <ProbabilityBar label="Away" probability={latestLive?.away_win ?? prediction.away_win} />

            <div className="section-title">Goals</div>
            {Object.entries(latestLive?.over_probabilities ?? prediction.over_probabilities).map(([line, p]) => (
              <ProbabilityBar key={line} label={`Over ${line}`} probability={p} />
            ))}
            <ProbabilityBar label="BTTS Yes" probability={latestLive?.btts_yes ?? prediction.btts_yes} />

            <MostLikelyBadge
              outcome={latestLive?.global_outcome ?? prediction.global_outcome}
              confidence={prediction.confidence}
            />
          </div>

          <div>
            <div className="section-title">Most likely scorelines</div>
            <ScoreGrid scores={prediction.correct_score_probabilities} />

            <div className="section-title">Form</div>
            <FormSummary label={match.home_team.name} form={stats.home_form} />
            <div style={{ height: 10 }} />
            <FormSummary label={match.away_team.name} form={stats.away_form} />
          </div>
        </div>

        <div className="section-title">Why this prediction</div>
        <ul className="factor-list">
          {prediction.explanation.positive.map((text) => (
            <li key={text} className="positive">
              + {text}
            </li>
          ))}
          {prediction.explanation.negative.map((text) => (
            <li key={text} className="negative">
              - {text}
            </li>
          ))}
        </ul>

        <div className="disclaimer">
          Model Probability: {(prediction.global_outcome.probability * 100).toFixed(1)}% &mdash; based on historical
          validation and model confidence, not a guaranteed outcome. Data quality:{" "}
          {(prediction.data_quality_score * 100).toFixed(0)}% &middot; Model agreement:{" "}
          {(prediction.model_agreement_score * 100).toFixed(0)}%
        </div>
      </div>
    </div>
  );
}
