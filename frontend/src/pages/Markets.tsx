import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { fetchOutcomes } from "../api";
import { ConfidenceTag } from "../components/MostLikelyOutcome";
import { EmptyState } from "../components/EmptyState";
import { ErrorState } from "../components/ErrorState";
import type { OutcomesResponse } from "../types";

/**
 * Every available betting outcome, grouped by league.
 *
 * The predictions table answers "what does the model say about this match".
 * This answers the question you actually start with: where is the best Over
 * 2.5 this week, which correct-score calls are worth a look, is there a
 * standout in La Liga. That needs outcomes compared across matches, which no
 * per-match view can do.
 */

const DAY_OPTIONS = [3, 7, 14];
const FLOORS = [
  { value: 0, label: "Any" },
  { value: 0.5, label: "50%+" },
  { value: 0.7, label: "70%+" },
  { value: 0.85, label: "85%+" },
];

export function Markets() {
  const [data, setData] = useState<OutcomesResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [market, setMarket] = useState<string>("");
  const [days, setDays] = useState(7);
  const [floor, setFloor] = useState(0);
  const [confidence, setConfidence] = useState<string>("");
  const navigate = useNavigate();

  useEffect(() => {
    let cancelled = false;
    setData(null);
    setError(null);
    fetchOutcomes({ market: market || undefined, days_ahead: days, min_probability: floor, confidence: confidence || undefined })
      .then((d) => !cancelled && setData(d))
      .catch((e) => !cancelled && setError(String(e instanceof Error ? e.message : e)));
    return () => {
      cancelled = true;
    };
  }, [market, days, floor, confidence]);

  // The market picker is built from what came back, so it can never offer a
  // market with nothing behind it. Kept from the unfiltered response, though,
  // or selecting one market would erase every other option from the list.
  const [knownMarkets, setKnownMarkets] = useState<OutcomesResponse["markets"]>([]);
  useEffect(() => {
    if (data && !market && !confidence && floor === 0) setKnownMarkets(data.markets);
  }, [data, market, confidence, floor]);

  const marketOptions = useMemo(
    () => (knownMarkets.length ? knownMarkets : data?.markets ?? []),
    [knownMarkets, data],
  );

  const selected = marketOptions.find((m) => m.market === market);

  return (
    <div>
      <div className="section-header">
        <h2>Betting markets</h2>
        <span className="meta">
          {data ? `${data.total_outcomes.toLocaleString()} outcomes across ${data.total_matches} matches` : "Loading…"}
        </span>
      </div>

      <div className="filter-bar" style={{ marginBottom: 14 }}>
        <select className="filter-select" value={market} onChange={(e) => setMarket(e.target.value)}>
          <option value="">All markets</option>
          {marketOptions.map((m) => (
            <option key={m.market} value={m.market}>
              {m.market}
            </option>
          ))}
        </select>

        {DAY_OPTIONS.map((d) => (
          <button key={d} className={`filter-chip${days === d ? " active" : ""}`} onClick={() => setDays(d)}>
            {d} days
          </button>
        ))}

        {FLOORS.map((f) => (
          <button key={f.value} className={`filter-chip${floor === f.value ? " active" : ""}`} onClick={() => setFloor(f.value)}>
            {f.label}
          </button>
        ))}

        <select className="filter-select" value={confidence} onChange={(e) => setConfidence(e.target.value)}>
          <option value="">Any confidence</option>
          <option value="HIGH">High only</option>
          <option value="MEDIUM">Medium only</option>
          <option value="LOW">Low only</option>
        </select>
      </div>

      {selected && (
        <p className="setting-note" style={{ marginBottom: 18 }}>
          <strong>{selected.market}</strong> — {selected.selections.join(" · ")}.{" "}
          {selected.mutually_exclusive
            ? "Exactly one of these happens, so their probabilities add up to 100%."
            : "These are not alternatives to each other — only one exact score can happen, and most matches land on none of the ones listed."}
        </p>
      )}

      {error && <ErrorState message={error} />}
      {!error && !data && <p className="badge-neutral">Loading outcomes…</p>}
      {!error && data && data.leagues.length === 0 && (
        <EmptyState icon="◌" title="Nothing matches these filters." />
      )}

      {!error &&
        data?.leagues.map((league) => (
          <div className="card card-pad" style={{ marginBottom: 20 }} key={league.league}>
            <div className="section-header" style={{ marginBottom: 12 }}>
              <h3 style={{ margin: 0 }}>{league.league}</h3>
              <span className="meta">
                {league.outcomes.length} outcomes · {league.matches} matches
              </span>
            </div>

            <div className="predictions-table-wrapper">
              <table className="predictions-table">
                <thead>
                  <tr>
                    <th>Match</th>
                    <th>Market</th>
                    <th>Selection</th>
                    <th>Probability</th>
                    <th>Confidence</th>
                    <th>Kickoff</th>
                  </tr>
                </thead>
                <tbody>
                  {league.outcomes.map((o, i) => (
                    <tr
                      key={`${o.match_id}-${o.market}-${o.selection}-${i}`}
                      onClick={() => navigate(`/app/match/${o.match_id}`)}
                      title={o.definition}
                    >
                      <td>
                        <div className="match-cell">
                          {o.home_team} vs {o.away_team}
                        </div>
                      </td>
                      <td className="sub">{o.market}</td>
                      <td>
                        <strong>{o.selection}</strong>
                      </td>
                      <td className="tabular-nums">{(o.probability * 100).toFixed(0)}%</td>
                      <td>
                        <ConfidenceTag confidence={o.confidence} />
                      </td>
                      <td className="sub">
                        {new Date(o.kickoff).toLocaleString(undefined, {
                          month: "short",
                          day: "numeric",
                          hour: "2-digit",
                          minute: "2-digit",
                        })}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ))}

      <p className="setting-note" style={{ marginTop: 4 }}>
        A high probability is not the same as a good bet, and outcomes from different markets can all
        happen in the same match — stacking them multiplies the risk, it doesn't add the confidence.
      </p>
    </div>
  );
}
