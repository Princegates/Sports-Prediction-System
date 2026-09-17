import { useEffect, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import {
  fetchHeadToHead,
  fetchLive,
  fetchMatch,
  fetchMatches,
  fetchPrediction,
  fetchPredictionHistory,
  fetchStatistics,
} from "../api";
import { AiExplanationPanel } from "../components/AiExplanationPanel";
import { AskAboutMatch } from "../components/AskAboutMatch";
import { ErrorState } from "../components/ErrorState";
import { FormStrip } from "../components/FormStrip";
import { AiScanningState } from "../components/LoadingSkeleton";
import { LiveEventControls } from "../components/LiveEventControls";
import { ModelTransparency } from "../components/ModelTransparency";
import { ConfidenceTag, MostLikelyOutcome } from "../components/MostLikelyOutcome";
import { MatchRow } from "../components/MatchRow";
import { PredictionBreakdown } from "../components/PredictionBreakdown";
import { ProbabilityBar } from "../components/ProbabilityBar";
import { ProbabilityTimeline } from "../components/ProbabilityTimeline";
import { RadialGauge } from "../components/RadialGauge";
import { ScoreHeatmap } from "../components/ScoreHeatmap";
import { Tabs } from "../components/Tabs";
import { TeamComparison } from "../components/TeamComparison";
import type { HeadToHeadMatch, LivePrediction, MatchStatistics, MatchSummary, ModelBreakdown, Prediction } from "../types";

const TAB_NAMES = ["Overview", "AI Prediction", "Form", "H2H", "Live", "Explanation"];

export function MatchDetail() {
  const { id } = useParams();
  const matchId = Number(id);
  const [searchParams, setSearchParams] = useSearchParams();
  const initialTab = searchParams.get("tab") ?? "Overview";
  const [tab, setTab] = useState(TAB_NAMES.includes(initialTab) ? initialTab : "Overview");

  const [match, setMatch] = useState<MatchSummary | null>(null);
  const [prediction, setPrediction] = useState<Prediction | null>(null);
  const [stats, setStats] = useState<MatchStatistics | null>(null);
  const [history, setHistory] = useState<Prediction[]>([]);
  const [live, setLive] = useState<LivePrediction[]>([]);
  const [h2h, setH2h] = useState<HeadToHeadMatch[] | null>(null);
  const [homeRecent, setHomeRecent] = useState<MatchSummary[]>([]);
  const [awayRecent, setAwayRecent] = useState<MatchSummary[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setMatch(null);
    setError(null);

    Promise.all([fetchMatch(matchId), fetchPrediction(matchId), fetchStatistics(matchId), fetchLive(matchId), fetchPredictionHistory(matchId)])
      .then(async ([m, p, s, l, hist]) => {
        if (cancelled) return;
        setMatch(m);
        setPrediction(p);
        setStats(s);
        setLive(l);
        setHistory(hist);

        const [h2hRows, homeMatches, awayMatches] = await Promise.all([
          fetchHeadToHead(m.home_team.id, m.away_team.id).catch(() => []),
          fetchMatches({ teamId: m.home_team.id }).catch(() => []),
          fetchMatches({ teamId: m.away_team.id }).catch(() => []),
        ]);
        if (cancelled) return;
        setH2h(h2hRows);
        const beforeKickoff = (list: MatchSummary[]) =>
          list.filter((x) => x.home_score !== null && new Date(x.date) < new Date(m.date)).sort((a, b) => (a.date < b.date ? 1 : -1)).slice(0, 5);
        setHomeRecent(beforeKickoff(homeMatches));
        setAwayRecent(beforeKickoff(awayMatches));
      })
      .catch((err) => !cancelled && setError(String(err)));

    return () => {
      cancelled = true;
    };
  }, [matchId]);

  const latestLive = live.length > 0 ? live[live.length - 1] : null;

  const timelinePoints = useMemo(() => {
    const points = history.map((p, i) => ({ label: `v${i + 1}`, value: p.home_win }));
    live.forEach((l) => points.push({ label: `${l.minute}'`, value: l.home_win }));
    return points;
  }, [history, live]);

  function changeTab(next: string) {
    setTab(next);
    setSearchParams(next === "Overview" ? {} : { tab: next });
  }

  if (error) return <ErrorState message={error} />;
  if (!match || !prediction || !stats) return <AiScanningState />;

  const breakdown = prediction.model_breakdown as ModelBreakdown;
  const usedSignals = ["Elo team-strength ratings", "Poisson expected-goals model"];
  if (breakdown.ml) usedSignals.push("Gradient-boosted form model");
  usedSignals.push("recent team form", "rest days");

  return (
    <div>
      <Link className="back-link" to="/">
        ← Back to dashboard
      </Link>

      <div className="match-header">
        <div>
          <h1>
            {match.home_team.name} vs {match.away_team.name}
          </h1>
          <div className="meta">
            {new Date(match.date).toLocaleString(undefined, { dateStyle: "full", timeStyle: "short" })}
            <span>&middot;</span>
            {match.league}
            <ConfidenceTag confidence={prediction.confidence} />
          </div>
        </div>
        <RadialGauge
          value={latestLive?.global_outcome.probability ?? prediction.global_outcome.probability}
          label={prediction.confidence}
          tone={prediction.confidence.toLowerCase() as "high" | "medium" | "low"}
        />
      </div>

      {latestLive && (
        <div className="live-strip">
          <span className="live-badge">
            <span className="live-dot" /> LIVE {latestLive.minute}'
          </span>
          <span className="score tabular-nums">
            {latestLive.score_home} - {latestLive.score_away}
          </span>
          <span style={{ color: "var(--text-secondary)" }}>Updated on {latestLive.trigger_event.replace(/_/g, " ")}</span>
        </div>
      )}

      <MostLikelyOutcome outcome={latestLive?.global_outcome ?? prediction.global_outcome} confidence={prediction.confidence} />

      <div style={{ height: 8 }} />
      <Tabs tabs={TAB_NAMES} active={tab} onChange={changeTab} />

      {tab === "Overview" && (
        <div className="two-col">
          <div className="card card-pad">
            <h3 style={{ marginBottom: 16, fontSize: 15 }}>Team comparison</h3>
            <TeamComparison homeForm={stats.home_form} awayForm={stats.away_form} />
          </div>
          <div className="card card-pad">
            <h3 style={{ marginBottom: 12, fontSize: 15 }}>Recent form</h3>
            <div style={{ marginBottom: 14 }}>
              <div className="match-meta-row" style={{ marginBottom: 6 }}>
                {match.home_team.name}
              </div>
              <FormStrip results={stats.home_form.recent_results} />
            </div>
            <div>
              <div className="match-meta-row" style={{ marginBottom: 6 }}>
                {match.away_team.name}
              </div>
              <FormStrip results={stats.away_form.recent_results} />
            </div>
          </div>
        </div>
      )}

      {tab === "AI Prediction" && (
        <div className="two-col">
          <div>
            <div className="card card-pad" style={{ marginBottom: 16 }}>
              <h3 style={{ marginBottom: 16, fontSize: 15 }}>Match result</h3>
              <ProbabilityBar label="Home" probability={prediction.home_win} variant="home" />
              <ProbabilityBar label="Draw" probability={prediction.draw} variant="draw" />
              <ProbabilityBar label="Away" probability={prediction.away_win} variant="away" />

              <h3 style={{ margin: "20px 0 16px", fontSize: 15 }}>Goals &amp; BTTS</h3>
              {Object.entries(prediction.over_probabilities).map(([line, p]) => (
                <ProbabilityBar key={line} label={`Over ${line}`} probability={p} />
              ))}
              <ProbabilityBar label="BTTS Yes" probability={prediction.btts_yes} />
            </div>

            <div className="card card-pad" style={{ marginBottom: 16 }}>
              <h3 style={{ marginBottom: 16, fontSize: 15 }}>Most likely scorelines</h3>
              <ScoreHeatmap scores={prediction.correct_score_probabilities} />
            </div>

            <div className="card card-pad">
              <h3 style={{ marginBottom: 4, fontSize: 15 }}>Prediction timeline</h3>
              <p style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 0 }}>
                Home win probability across every stored snapshot for this match.
              </p>
              <ProbabilityTimeline points={timelinePoints} />
            </div>
          </div>

          <div>
            <div className="card card-pad" style={{ marginBottom: 16, display: "flex", justifyContent: "center" }}>
              <RadialGauge value={prediction.global_outcome.probability} label="AI Confidence" tone={prediction.confidence.toLowerCase() as "high" | "medium" | "low"} size={140} strokeWidth={12} />
            </div>
            <div className="card card-pad" style={{ marginBottom: 16 }}>
              <h3 style={{ marginBottom: 14, fontSize: 15 }}>AI signal analysis</h3>
              <PredictionBreakdown
                breakdown={breakdown}
                modelAgreement={prediction.model_agreement_score}
                dataQuality={prediction.data_quality_score}
                homeForm={stats.home_form}
                awayForm={stats.away_form}
              />
            </div>
            <div className="card card-pad">
              <h3 style={{ marginBottom: 14, fontSize: 15 }}>How the AI calculated this</h3>
              <ModelTransparency hasMlModel={Boolean(breakdown.ml)} />
            </div>
          </div>
        </div>
      )}

      {tab === "Form" && (
        <div className="two-col">
          <div className="card card-pad">
            <h3 style={{ marginBottom: 8, fontSize: 15 }}>{match.home_team.name}</h3>
            <FormStrip results={stats.home_form.recent_results} />
            <div style={{ marginTop: 14, display: "flex", flexDirection: "column", gap: 6 }}>
              {homeRecent.length === 0 && <p className="badge-neutral">No prior matches in our data.</p>}
              {homeRecent.map((m) => (
                <MatchRow key={m.id} match={m} perspectiveTeamId={match.home_team.id} />
              ))}
            </div>
          </div>
          <div className="card card-pad">
            <h3 style={{ marginBottom: 8, fontSize: 15 }}>{match.away_team.name}</h3>
            <FormStrip results={stats.away_form.recent_results} />
            <div style={{ marginTop: 14, display: "flex", flexDirection: "column", gap: 6 }}>
              {awayRecent.length === 0 && <p className="badge-neutral">No prior matches in our data.</p>}
              {awayRecent.map((m) => (
                <MatchRow key={m.id} match={m} perspectiveTeamId={match.away_team.id} />
              ))}
            </div>
          </div>
        </div>
      )}

      {tab === "H2H" && (
        <div className="card card-pad">
          <h3 style={{ marginBottom: 14, fontSize: 15 }}>Head-to-head</h3>
          {h2h === null && <p className="badge-neutral">Loading…</p>}
          {h2h !== null && h2h.length === 0 && <p className="badge-neutral">These two teams haven't met in our imported data.</p>}
          {h2h !== null && h2h.length > 0 && (
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {h2h.map((m, i) => (
                <div key={i} className="transparency-row">
                  <span className="name" style={{ width: "auto", flex: 1 }}>
                    {m.home_team} {m.home_score} - {m.away_score} {m.away_team}
                  </span>
                  <span className="pct" style={{ width: "auto" }}>
                    {new Date(m.date).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" })}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {tab === "Live" && (
        <div className="two-col">
          <LiveEventControls
            matchId={match.id}
            currentMinute={latestLive?.minute ?? 1}
            currentHome={latestLive?.score_home ?? match.home_score ?? 0}
            currentAway={latestLive?.score_away ?? match.away_score ?? 0}
            onEvent={(result) => setLive((prev) => [...prev, result])}
          />
          <div className="card card-pad">
            <h3 style={{ marginBottom: 4, fontSize: 15 }}>Live probability timeline</h3>
            {live.length === 0 ? (
              <p className="badge-neutral">No live events recorded yet for this match.</p>
            ) : (
              <ProbabilityTimeline points={live.map((l) => ({ label: `${l.minute}'`, value: l.home_win }))} />
            )}
          </div>
        </div>
      )}

      {tab === "Explanation" && (
        <div className="two-col">
          <div className="card card-pad">
            <h3 style={{ marginBottom: 6, fontSize: 15 }}>Why does the model favor this outcome?</h3>
            <p style={{ fontSize: 13, color: "var(--text-secondary)", marginTop: 0 }}>
              The AI combined signals from {usedSignals.length} sources: {usedSignals.join(", ")}.
            </p>
            <AiExplanationPanel positive={prediction.explanation.positive} negative={prediction.explanation.negative} />
          </div>
          <div className="card card-pad">
            <h3 style={{ marginBottom: 4, fontSize: 15 }}>Ask about this match</h3>
            <AskAboutMatch
              context={{
                homeTeam: match.home_team.name,
                awayTeam: match.away_team.name,
                prediction,
                homeForm: stats.home_form,
                awayForm: stats.away_form,
                latestLive,
              }}
            />
          </div>
        </div>
      )}

      <div className="disclaimer">
        Model Probability: {(prediction.global_outcome.probability * 100).toFixed(1)}% -- based on historical
        validation and model confidence, not a guaranteed outcome. Data quality: {(prediction.data_quality_score * 100).toFixed(0)}% &middot;
        Model agreement: {(prediction.model_agreement_score * 100).toFixed(0)}%
      </div>
    </div>
  );
}
