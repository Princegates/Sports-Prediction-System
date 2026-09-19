import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { fetchMatch, fetchMostLikely } from "../api";
import { LEAGUES } from "../components/AppShell";
import { CopyButton } from "../components/CopyButton";
import { DateStrip, localDayKey } from "../components/DateStrip";
import { ConfidenceTag } from "../components/MostLikelyOutcome";
import { ErrorState } from "../components/ErrorState";
import { EmptyState } from "../components/EmptyState";
import { formatSelection, formatSelections } from "../lib/copySelections";
import type { MatchSummary, Prediction } from "../types";

interface Row {
  prediction: Prediction;
  match: MatchSummary;
}

type ConfidenceFilter = "ALL" | "HIGH" | "MEDIUM" | "LOW";

// How far ahead to load. The refresh workflow generates predictions 10 days
// out by default, so asking for more returns nothing extra.
//
// Everything in that window is fetched once and filtered in the browser.
// Re-fetching per day range, which is what this page used to do, made
// switching between "today" and "this week" a round trip to Frankfurt for
// data already on the page.
const HORIZON_DAYS = 10;
type SortKey = "kickoff" | "probability" | "confidence";

const CONFIDENCE_RANK = { HIGH: 3, MEDIUM: 2, LOW: 1 };

export function Predictions() {
  const [league, setLeague] = useState<string>("ALL");
  const [day, setDay] = useState<string | null>(null);
  const [confidence, setConfidence] = useState<ConfidenceFilter>("ALL");
  const [sortKey, setSortKey] = useState<SortKey>("kickoff");
  const [sortDir, setSortDir] = useState<1 | -1>(1);
  const [rows, setRows] = useState<Row[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    let cancelled = false;
    setRows(null);
    setError(null);

    const leaguesToFetch = league === "ALL" ? LEAGUES : [league];
    Promise.all(leaguesToFetch.map((l) => fetchMostLikely(l, HORIZON_DAYS, 80).catch(() => [])))
      .then(async (perLeague) => {
        if (cancelled) return;
        const predictions = perLeague.flat();
        const matches = await Promise.all(predictions.map((p) => fetchMatch(p.match_id)));
        if (cancelled) return;
        setRows(predictions.map((prediction, i) => ({ prediction, match: matches[i] })));
      })
      .catch((err) => !cancelled && setError(String(err)));

    return () => {
      cancelled = true;
    };
  }, [league]);

  // Counts on the strip reflect the league filter but not the day or
  // confidence filters -- a day showing "3" must still show 3 once you
  // select it, and a count that changed when you clicked it would be
  // useless for deciding where to click next.
  const kickoffs = useMemo(() => (rows ? rows.map((r) => new Date(r.match.date)) : []), [rows]);

  const visible = useMemo(() => {
    if (!rows) return null;
    let filtered = confidence === "ALL" ? rows : rows.filter((r) => r.prediction.confidence === confidence);
    if (day) filtered = filtered.filter((r) => localDayKey(new Date(r.match.date)) === day);
    filtered = [...filtered].sort((a, b) => {
      let diff = 0;
      if (sortKey === "kickoff") diff = new Date(a.match.date).getTime() - new Date(b.match.date).getTime();
      if (sortKey === "probability") diff = a.prediction.global_outcome.probability - b.prediction.global_outcome.probability;
      if (sortKey === "confidence") diff = CONFIDENCE_RANK[a.prediction.confidence] - CONFIDENCE_RANK[b.prediction.confidence];
      return diff * sortDir;
    });
    return filtered;
  }, [rows, confidence, day, sortKey, sortDir]);

  function toggleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir((d) => (d === 1 ? -1 : 1));
    } else {
      setSortKey(key);
      setSortDir(1);
    }
  }

  return (
    <div>
      <div className="section-header">
        <h2>AI Predictions</h2>
        <div style={{ display: "flex", alignItems: "baseline", gap: 12 }}>
          <span className="meta">{visible ? `${visible.length} matches` : "Loading..."}</span>
          {visible && visible.length > 0 && (
            <CopyButton text={formatSelections(visible)} label="Copy all selections" />
          )}
        </div>
      </div>

      <div className="filter-bar" style={{ marginBottom: 20 }}>
        <select className="filter-select" value={league} onChange={(e) => setLeague(e.target.value)}>
          <option value="ALL">All leagues</option>
          {LEAGUES.map((l) => (
            <option key={l} value={l}>
              {l}
            </option>
          ))}
        </select>
        {(["ALL", "HIGH", "MEDIUM", "LOW"] as ConfidenceFilter[]).map((c) => (
          <button key={c} className={`filter-chip${confidence === c ? " active" : ""}`} onClick={() => setConfidence(c)}>
            {c === "ALL" ? "Any confidence" : `${c.charAt(0)}${c.slice(1).toLowerCase()} confidence`}
          </button>
        ))}
      </div>

      <DateStrip dates={kickoffs} horizonDays={HORIZON_DAYS} value={day} onChange={setDay} />

      {error && <ErrorState message={error} />}
      {!error && visible === null && <p className="badge-neutral">Loading predictions…</p>}
      {!error && visible !== null && visible.length === 0 && (
        <EmptyState icon="◌" title="No predictions match these filters." />
      )}
      {!error && visible !== null && visible.length > 0 && (
        <div className="predictions-table-wrapper">
          <table className="predictions-table">
            <thead>
              <tr>
                <th>Match</th>
                <th>AI Outcome</th>
                <th onClick={() => toggleSort("probability")}>Probability {sortKey === "probability" ? (sortDir === 1 ? "↑" : "↓") : ""}</th>
                <th onClick={() => toggleSort("confidence")}>Confidence {sortKey === "confidence" ? (sortDir === 1 ? "↑" : "↓") : ""}</th>
                <th onClick={() => toggleSort("kickoff")}>Kickoff {sortKey === "kickoff" ? (sortDir === 1 ? "↑" : "↓") : ""}</th>
                <th>Status</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {visible.map(({ prediction, match }) => (
                <tr key={prediction.match_id} onClick={() => navigate(`/app/match/${prediction.match_id}`)}>
                  <td>
                    <div className="match-cell">
                      {match.home_team.name} vs {match.away_team.name}
                    </div>
                    <div className="sub">{match.league}</div>
                  </td>
                  <td>{prediction.global_outcome.selection}</td>
                  <td className="tabular-nums">{(prediction.global_outcome.probability * 100).toFixed(0)}%</td>
                  <td>
                    <ConfidenceTag confidence={prediction.confidence} />
                  </td>
                  <td>{new Date(match.date).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}</td>
                  <td>{match.status}</td>
                  <td>
                    <CopyButton text={formatSelection(match, prediction)} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
