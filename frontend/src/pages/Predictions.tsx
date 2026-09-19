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
import { downloadCsv, toCsv } from "../lib/csvExport";
import type { MatchSummary, Prediction } from "../types";

interface Row {
  prediction: Prediction;
  match: MatchSummary;
}

type ConfidenceFilter = "ALL" | "HIGH" | "MEDIUM" | "LOW";
type ViewMode = "table" | "tiers";

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

// Confidence tiers: each match's own probability, unmodified, bucketed for
// browsing from near-certain down to high-risk. Deliberately not a way to
// combine several picks into one -- multiplying independent probabilities
// together (what an accumulator does) turns three 90% calls into a ~73%
// one, and presenting that product as a single confidence number is the
// exact thing the Markets page's "stacking multiplies risk" note warns
// against. Every row here stands alone.
const TIERS: { min: number; max: number; label: string; note?: string }[] = [
  { min: 0.95, max: 1.001, label: "95–100%", note: "Near certain" },
  { min: 0.9, max: 0.95, label: "90–95%" },
  { min: 0.8, max: 0.9, label: "80–90%" },
  { min: 0.7, max: 0.8, label: "70–80%" },
  { min: 0.6, max: 0.7, label: "60–70%" },
  { min: 0.5, max: 0.6, label: "50–60%" },
  { min: 0, max: 0.5, label: "Below 50%", note: "High risk" },
];

export function Predictions() {
  const [league, setLeague] = useState<string>("ALL");
  const [day, setDay] = useState<string | null>(null);
  const [confidence, setConfidence] = useState<ConfidenceFilter>("ALL");
  const [sortKey, setSortKey] = useState<SortKey>("kickoff");
  const [sortDir, setSortDir] = useState<1 | -1>(1);
  const [viewMode, setViewMode] = useState<ViewMode>("table");
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

  const tieredGroups = useMemo(() => {
    if (!visible) return [];
    const byProbabilityDesc = [...visible].sort(
      (a, b) => b.prediction.global_outcome.probability - a.prediction.global_outcome.probability,
    );
    return TIERS.map((tier) => ({
      ...tier,
      rows: byProbabilityDesc.filter(
        (r) => r.prediction.global_outcome.probability >= tier.min && r.prediction.global_outcome.probability < tier.max,
      ),
    })).filter((tier) => tier.rows.length > 0);
  }, [visible]);

  function toggleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir((d) => (d === 1 ? -1 : 1));
    } else {
      setSortKey(key);
      setSortDir(1);
    }
  }

  function renderTable(rowsToShow: Row[]) {
    return (
      <div className="predictions-table-wrapper">
        <table className="predictions-table">
          <thead>
            <tr>
              <th>Match</th>
              <th>AI Outcome</th>
              <th onClick={viewMode === "table" ? () => toggleSort("probability") : undefined}>
                Probability {viewMode === "table" && sortKey === "probability" ? (sortDir === 1 ? "↑" : "↓") : ""}
              </th>
              <th onClick={viewMode === "table" ? () => toggleSort("confidence") : undefined}>
                Confidence {viewMode === "table" && sortKey === "confidence" ? (sortDir === 1 ? "↑" : "↓") : ""}
              </th>
              <th onClick={viewMode === "table" ? () => toggleSort("kickoff") : undefined}>
                Kickoff {viewMode === "table" && sortKey === "kickoff" ? (sortDir === 1 ? "↑" : "↓") : ""}
              </th>
              <th>Status</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rowsToShow.map(({ prediction, match }) => (
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
    );
  }

  return (
    <div>
      <div className="section-header">
        <h2>AI Predictions</h2>
        <div style={{ display: "flex", alignItems: "baseline", gap: 12 }}>
          <span className="meta">{visible ? `${visible.length} matches` : "Loading..."}</span>
          {visible && visible.length > 0 && (
            <>
              <CopyButton text={formatSelections(visible)} label="Copy all selections" />
              <button
                type="button"
                className="btn ghost"
                onClick={() => downloadCsv(`predictions-${day ?? "all"}.csv`, toCsv(visible))}
              >
                Export CSV
              </button>
            </>
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
        <span style={{ width: 1, alignSelf: "stretch", background: "var(--border)", margin: "0 4px" }} />
        <button className={`filter-chip${viewMode === "table" ? " active" : ""}`} onClick={() => setViewMode("table")}>
          Table
        </button>
        <button className={`filter-chip${viewMode === "tiers" ? " active" : ""}`} onClick={() => setViewMode("tiers")}>
          Confidence tiers
        </button>
      </div>

      <DateStrip dates={kickoffs} horizonDays={HORIZON_DAYS} value={day} onChange={setDay} />

      {error && <ErrorState message={error} />}
      {!error && visible === null && <p className="badge-neutral">Loading predictions…</p>}
      {!error && visible !== null && visible.length === 0 && (
        <EmptyState icon="◌" title="No predictions match these filters." />
      )}

      {!error && visible !== null && visible.length > 0 && viewMode === "table" && renderTable(visible)}

      {!error && visible !== null && visible.length > 0 && viewMode === "tiers" && (
        <>
          <p className="setting-note" style={{ marginBottom: 16 }}>
            Each match's own probability, grouped from near-certain down to high-risk. These are not
            combined into one bet -- stacking several picks together multiplies the risk, it doesn't
            add the confidence.
          </p>
          {tieredGroups.map((tier) => (
            <div key={tier.label} style={{ marginBottom: 24 }}>
              <div className="section-header" style={{ marginBottom: 8 }}>
                <h3 style={{ margin: 0 }}>
                  {tier.label}
                  {tier.note && <span className="sub" style={{ marginLeft: 8 }}>{tier.note}</span>}
                </h3>
                <span className="meta">{tier.rows.length} match{tier.rows.length === 1 ? "" : "es"}</span>
              </div>
              {renderTable(tier.rows)}
            </div>
          ))}
        </>
      )}
    </div>
  );
}
