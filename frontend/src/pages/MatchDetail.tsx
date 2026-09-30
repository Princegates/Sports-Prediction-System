import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  featurePick,
  fetchFeaturedPicksAdmin,
  fetchHeadToHead,
  fetchLive,
  fetchMatch,
  fetchMatches,
  fetchMatchOutcomes,
  fetchPrediction,
  fetchPredictionHistory,
  fetchStatistics,
  recordMatchView,
  unfeaturePick,
  updateFeaturedPickNote,
} from "../api";
import { AiExplanationPanel } from "../components/AiExplanationPanel";
import { AskAboutMatch } from "../components/AskAboutMatch";
import { CopyButton } from "../components/CopyButton";
import { EmptyState } from "../components/EmptyState";
import { ErrorState } from "../components/ErrorState";
import { FormStrip } from "../components/FormStrip";
import { GoalCelebration } from "../components/GoalCelebration";
import { HotPickBadge } from "../components/HotPickBadge";
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
import { formatSelection } from "../lib/copySelections";
import { isHotPick } from "../lib/filters";
import { useAuth } from "../lib/AuthContext";
import type {
  BetCodePick,
  FeaturedPick,
  HeadToHeadMatch,
  LivePrediction,
  MatchStatistics,
  MatchSummary,
  ModelBreakdown,
  OutcomesResponse,
  Prediction,
} from "../types";

const TAB_NAMES = ["Overview", "AI Prediction", "Form", "H2H", "Live", "Markets", "Explanation"];

export function MatchDetail() {
  const { user } = useAuth();
  const { id } = useParams();
  const matchId = Number(id);
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const initialTab = searchParams.get("tab") ?? "Overview";
  const [tab, setTab] = useState(TAB_NAMES.includes(initialTab) ? initialTab : "Overview");

  const [match, setMatch] = useState<MatchSummary | null>(null);
  const [prediction, setPrediction] = useState<Prediction | null>(null);
  const [stats, setStats] = useState<MatchStatistics | null>(null);
  const [history, setHistory] = useState<Prediction[]>([]);
  const [live, setLive] = useState<LivePrediction[]>([]);
  const [outcomes, setOutcomes] = useState<OutcomesResponse | null>(null);
  const [h2h, setH2h] = useState<HeadToHeadMatch[] | null>(null);
  const [homeRecent, setHomeRecent] = useState<MatchSummary[]>([]);
  const [awayRecent, setAwayRecent] = useState<MatchSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [goalTrigger, setGoalTrigger] = useState(0);
  const [goalTeam, setGoalTeam] = useState("");
  const [featuredPicks, setFeaturedPicks] = useState<FeaturedPick[]>([]);
  const [pickBusy, setPickBusy] = useState<string | null>(null);
  // A match contributes at most one leg to a slip -- two outcomes on the same
  // fixture are correlated, not independent, and the booking engine only
  // keeps the first ref for a match anyway. So picking a market here replaces
  // any earlier pick from this same page rather than adding alongside it.
  const [selectedPick, setSelectedPick] = useState<{ market: string; selection: string } | null>(null);

  useEffect(() => {
    let cancelled = false;
    setMatch(null);
    setError(null);

    Promise.all([
      fetchMatch(matchId),
      fetchPrediction(matchId),
      fetchStatistics(matchId),
      fetchLive(matchId),
      fetchPredictionHistory(matchId),
      fetchMatchOutcomes(matchId).catch(() => null),
    ])
      .then(async ([m, p, s, l, hist, allOutcomes]) => {
        if (cancelled) return;
        setMatch(m);
        setPrediction(p);
        setStats(s);
        setLive(l);
        setHistory(hist);
        setOutcomes(allOutcomes);

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
        recordMatchView(matchId).catch(() => {
          // best-effort -- history is a convenience, never blocks the page
        });
      })
      .catch((err) => !cancelled && setError(String(err)));

    return () => {
      cancelled = true;
    };
  }, [matchId]);

  useEffect(() => {
    if (user?.role !== "superadmin") return;
    let cancelled = false;
    fetchFeaturedPicksAdmin()
      .then((picks) => !cancelled && setFeaturedPicks(picks))
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [user?.role, matchId]);

  function findFeatured(market: string, selection: string): FeaturedPick | undefined {
    return featuredPicks.find((p) => p.match.id === matchId && p.market === market && p.selection === selection);
  }

  async function handleToggleFeature(market: string, selection: string) {
    const key = `${market}|${selection}`;
    const existing = findFeatured(market, selection);
    setPickBusy(key);
    try {
      if (existing) {
        await unfeaturePick(existing.id);
        setFeaturedPicks((prev) => prev.filter((p) => p.id !== existing.id));
      } else {
        const raw = window.prompt("Optional note for this pick (shown on every Dashboard) -- Cancel to skip featuring it:", "");
        if (raw === null) return;
        const created = await featurePick({ match_id: matchId, market, selection, note: raw || undefined });
        setFeaturedPicks((prev) => [...prev, created]);
      }
    } catch (err) {
      window.alert(String(err instanceof Error ? err.message : err));
    } finally {
      setPickBusy(null);
    }
  }

  function togglePick(market: string, selection: string) {
    setSelectedPick((prev) => (prev && prev.market === market && prev.selection === selection ? null : { market, selection }));
  }

  function sendPickToGeneration() {
    if (!selectedPick) return;
    const picks: BetCodePick[] = [{ match_id: matchId, market: selectedPick.market, selection: selectedPick.selection }];
    navigate("/app/betcodes", { state: { picks } });
  }

  async function handleEditFeaturedNote(pick: FeaturedPick) {
    const key = `${pick.market}|${pick.selection}`;
    const raw = window.prompt("Edit note for this pick (shown on every Dashboard):", pick.note ?? "");
    if (raw === null) return;
    setPickBusy(key);
    try {
      const updated = await updateFeaturedPickNote(pick.id, raw);
      setFeaturedPicks((prev) => prev.map((p) => (p.id === pick.id ? updated : p)));
    } catch (err) {
      window.alert(String(err instanceof Error ? err.message : err));
    } finally {
      setPickBusy(null);
    }
  }

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

  // A one-finger swipe across the tab panel steps to the adjacent tab --
  // the tab strip itself already scrolls, but on a phone reaching a tab
  // several taps to the right of the current one otherwise means tapping
  // back to the strip for every single step. Mouse drags don't fire touch
  // events, so this is a no-op on desktop.
  const touchStart = useRef<{ x: number; y: number } | null>(null);
  function handlePanelTouchStart(e: React.TouchEvent) {
    touchStart.current = { x: e.touches[0].clientX, y: e.touches[0].clientY };
  }
  function handlePanelTouchEnd(e: React.TouchEvent) {
    const start = touchStart.current;
    touchStart.current = null;
    if (!start) return;
    const dx = e.changedTouches[0].clientX - start.x;
    const dy = e.changedTouches[0].clientY - start.y;
    // A short drag is a tap or a scroll correction, not a swipe; a mostly
    // vertical one is the page scrolling, not a tab change.
    if (Math.abs(dx) < 60 || Math.abs(dx) < Math.abs(dy) * 1.5) return;
    const idx = TAB_NAMES.indexOf(tab);
    if (dx < 0 && idx < TAB_NAMES.length - 1) changeTab(TAB_NAMES[idx + 1]);
    else if (dx > 0 && idx > 0) changeTab(TAB_NAMES[idx - 1]);
  }

  if (error) return <ErrorState message={error} />;
  if (!match || !prediction || !stats) return <AiScanningState />;

  const breakdown = prediction.model_breakdown as ModelBreakdown;
  const usedSignals = ["Elo team-strength ratings", "Poisson expected-goals model"];
  if (breakdown.ml) usedSignals.push("Gradient-boosted form model");
  usedSignals.push("recent team form", "rest days");

  return (
    <div>
      <GoalCelebration triggerKey={goalTrigger} team={goalTeam} />
      <Link className="back-link" to="/app">
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

      <MostLikelyOutcome
        outcome={latestLive?.global_outcome ?? prediction.global_outcome}
        confidence={prediction.confidence}
        dataQuality={prediction.data_quality_score}
        modelAgreement={prediction.model_agreement_score}
      />

      <div style={{ display: "flex", justifyContent: "flex-end", margin: "8px 0" }}>
        <CopyButton text={formatSelection(match, prediction)} label="Copy this pick" />
      </div>

      <div style={{ height: 8 }} />
      <Tabs tabs={TAB_NAMES} active={tab} onChange={changeTab} />

      <div onTouchStart={handlePanelTouchStart} onTouchEnd={handlePanelTouchEnd} style={{ touchAction: "pan-y" }}>
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
              <ModelTransparency hasMlModel={Boolean(breakdown.ml)} weights={breakdown.weights} />
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
            onEvent={(result) => {
              if (result.trigger_event === "goal") {
                const prevHome = latestLive?.score_home ?? match.home_score ?? 0;
                const prevAway = latestLive?.score_away ?? match.away_score ?? 0;
                if (result.score_home > prevHome) setGoalTeam(match.home_team.name);
                else if (result.score_away > prevAway) setGoalTeam(match.away_team.name);
                setGoalTrigger((k) => k + 1);
              }
              setLive((prev) => [...prev, result]);
            }}
            canClear={user?.role === "superadmin"}
            hasEvents={live.length > 0}
            onCleared={(resetMatch) => {
              setLive([]);
              setMatch(resetMatch);
            }}
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

      {tab === "Markets" && (
        <div className="card card-pad">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: 12 }}>
            <div>
              <h3 style={{ marginBottom: 4, fontSize: 15 }}>Every market for this fixture</h3>
              <p style={{ fontSize: 13, color: "var(--text-secondary)", marginTop: 0, marginBottom: 4 }}>
                Grouped the way the model groups them: selections in the same group are mutually exclusive and add
                up to 100%, except Correct Score -- most matches land on none of the scores listed.
              </p>
              <p style={{ fontSize: 13, color: "var(--text-secondary)", marginTop: 0, marginBottom: 16 }}>
                Check a selection to use it as this match's leg in AI Generation -- checking another replaces it,
                since a slip only takes one pick per match.
              </p>
            </div>
            {selectedPick && (
              <div style={{ display: "flex", gap: 8, flexShrink: 0 }}>
                <button type="button" className="btn ghost" onClick={() => setSelectedPick(null)}>
                  Clear selection
                </button>
                <button type="button" className="btn" onClick={sendPickToGeneration}>
                  Send pick to AI Generation
                </button>
              </div>
            )}
          </div>
          {!outcomes && <p className="badge-neutral">Loading markets…</p>}
          {outcomes && outcomes.markets.length === 0 && (
            <EmptyState icon="◌" title="Not enough history yet to offer markets for this fixture." />
          )}
          {outcomes &&
            outcomes.markets.map((m) => {
              const rows = outcomes.leagues[0]?.outcomes.filter((o) => o.market === m.market) ?? [];
              return (
                <div key={m.market} style={{ marginBottom: 20 }}>
                  <div className="match-meta-row" style={{ marginBottom: 6 }}>
                    <strong>{m.market}</strong>
                    <span className="sub" style={{ marginLeft: 8 }}>
                      {m.mutually_exclusive ? "sums to 100%" : "not mutually exclusive"}
                    </span>
                  </div>
                  <div className="predictions-table-wrapper">
                    <table className="predictions-table">
                      <tbody>
                        {[...rows]
                          .sort((a, b) => b.probability - a.probability)
                          .map((o) => {
                            const featured = user?.role === "superadmin" ? findFeatured(o.market, o.selection) : undefined;
                            const key = `${o.market}|${o.selection}`;
                            const checked = selectedPick?.market === o.market && selectedPick?.selection === o.selection;
                            return (
                              <tr key={o.selection} title={o.definition}>
                                <td style={{ width: 28 }}>
                                  <input
                                    type="checkbox"
                                    checked={checked}
                                    onChange={() => togglePick(o.market, o.selection)}
                                    aria-label={`Use ${o.selection} (${o.market}) as this match's leg in AI Generation`}
                                  />
                                </td>
                                <td>
                                  {o.selection}
                                  {isHotPick(o.probability, o.data_quality_score, o.model_agreement_score) && (
                                    <span style={{ marginLeft: 6 }}>
                                      <HotPickBadge compact />
                                    </span>
                                  )}
                                </td>
                                <td className="tabular-nums" style={{ width: 80 }}>
                                  {(o.probability * 100).toFixed(0)}%
                                </td>
                                {user?.role === "superadmin" && (
                                  <td style={{ width: 140 }}>
                                    <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                                      {featured && (
                                        <button
                                          className="btn ghost"
                                          style={{ padding: "2px 8px", fontSize: 12 }}
                                          disabled={pickBusy === key}
                                          onClick={() => handleEditFeaturedNote(featured)}
                                        >
                                          ✎ Edit note
                                        </button>
                                      )}
                                      <button
                                        className="btn ghost"
                                        style={{ padding: "2px 8px", fontSize: 12 }}
                                        disabled={pickBusy === key}
                                        onClick={() => handleToggleFeature(o.market, o.selection)}
                                      >
                                        {featured ? "★ Unfeature" : "☆ Feature"}
                                      </button>
                                    </div>
                                  </td>
                                )}
                              </tr>
                            );
                          })}
                      </tbody>
                    </table>
                  </div>
                </div>
              );
            })}
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
      </div>

      <div className="disclaimer">
        Model Probability: {(prediction.global_outcome.probability * 100).toFixed(1)}% -- based on historical
        validation and model confidence, not a guaranteed outcome. Data quality: {(prediction.data_quality_score * 100).toFixed(0)}% &middot;
        Model agreement: {(prediction.model_agreement_score * 100).toFixed(0)}%
      </div>
    </div>
  );
}
