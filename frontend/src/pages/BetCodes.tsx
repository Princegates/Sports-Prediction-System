import { useEffect, useState } from "react";
import { fetchBetCodeHistory, generateBetCode, previewBetCode } from "../api";
import { CopyButton } from "../components/CopyButton";
import { EmptyState } from "../components/EmptyState";
import { ErrorState } from "../components/ErrorState";
import { LEAGUES } from "../components/AppShell";
import type { BetCode, BetCodeCriteria, BetCodePreview } from "../types";

/**
 * Builds a multi-match selection toward a target combined price and, if an
 * aggregator is configured, turns it into a real bookmaker booking code.
 *
 * Two steps on purpose. Preview only ever reads -- it costs nothing and can
 * be re-run freely while narrowing target odds or accuracy. Generate is the
 * one call that may spend an aggregator request, and it always sends back
 * exactly the legs preview showed, never a silently re-run selection that
 * could differ from what was on screen.
 *
 * No code is ever shown that this platform did not receive from a real
 * aggregator. When none is configured, the selections and combined price --
 * both real -- are still shown, with a plain explanation of what's missing
 * rather than a fabricated string that would fail the moment someone tried
 * to use it.
 */

const BOOKMAKERS = ["Bet9ja", "SportyBet", "1xBet", "Betway", "MSport", "Premier Bet"];

const MARKET_OPTIONS = [
  { value: "Match Result", label: "Match result (1X2)" },
  { value: "Both Teams To Score", label: "Both teams to score" },
  { value: "Total Goals 2.5", label: "Over/Under 2.5 goals" },
];

const ACCURACY_OPTIONS = [
  { value: 0.5, label: "50%+" },
  { value: 0.65, label: "65%+" },
  { value: 0.75, label: "75%+" },
  { value: 0.85, label: "85%+" },
];

function statusLabel(status: BetCode["status"]): string {
  switch (status) {
    case "code_ready":
      return "Code generated";
    case "provider_unavailable":
      return "No aggregator configured";
    case "provider_error":
      return "Aggregator error";
    default:
      return "Selected";
  }
}

export function BetCodes() {
  const [bookmaker, setBookmaker] = useState(BOOKMAKERS[0]);
  const [targetOdds, setTargetOdds] = useState(3.0);
  const [markets, setMarkets] = useState<string[]>([]);
  const [minProbability, setMinProbability] = useState(0.65);
  const [league, setLeague] = useState("");
  const [daysAhead, setDaysAhead] = useState(7);

  const [preview, setPreview] = useState<BetCodePreview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [previewing, setPreviewing] = useState(false);

  const [result, setResult] = useState<BetCode | null>(null);
  const [generateError, setGenerateError] = useState<string | null>(null);
  const [generating, setGenerating] = useState(false);

  const [history, setHistory] = useState<BetCode[] | null>(null);

  function loadHistory() {
    fetchBetCodeHistory().then(setHistory).catch(() => setHistory([]));
  }
  useEffect(loadHistory, []);

  function criteria(): BetCodeCriteria {
    return {
      bookmaker,
      target_odds: targetOdds,
      markets,
      min_probability: minProbability,
      league: league || null,
      days_ahead: daysAhead,
    };
  }

  async function handlePreview() {
    setPreviewing(true);
    setPreviewError(null);
    setResult(null);
    try {
      setPreview(await previewBetCode(criteria()));
    } catch (e) {
      setPreview(null);
      setPreviewError(String(e instanceof Error ? e.message : e));
    } finally {
      setPreviewing(false);
    }
  }

  async function handleGenerate() {
    if (!preview) return;
    setGenerating(true);
    setGenerateError(null);
    try {
      const slip = await generateBetCode(criteria(), preview.legs);
      setResult(slip);
      loadHistory();
    } catch (e) {
      setGenerateError(String(e instanceof Error ? e.message : e));
    } finally {
      setGenerating(false);
    }
  }

  function toggleMarket(value: string) {
    setMarkets((m) => (m.includes(value) ? m.filter((v) => v !== value) : [...m, value]));
  }

  return (
    <div>
      <div className="section-header">
        <h2>Booking codes</h2>
        <span className="meta">Combine matches toward a target price, priced from real bookmaker odds</span>
      </div>

      <p className="setting-note" style={{ marginBottom: 16 }}>
        Every leg here is priced from a real, stored bookmaker quote -- there is no estimated or synthetic
        price. Combining matches multiplies the risk as fast as it multiplies the price: three legs each
        70% likely land around a 34% chance of all three coming in, whatever the combined odds look like.
        The number below is calculated, not softened.
      </p>

      <div className="card card-pad" style={{ marginBottom: 20 }}>
        <div className="auth-form" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 12 }}>
          <label>
            Bookmaker
            <select value={bookmaker} onChange={(e) => setBookmaker(e.target.value)}>
              {BOOKMAKERS.map((b) => (
                <option key={b} value={b}>
                  {b}
                </option>
              ))}
            </select>
            <span className="sub" style={{ fontWeight: 400 }}>
              Who the code is generated for. Prices come from whichever bookmaker this project has a
              stored quote from -- see "Priced by" per leg below.
            </span>
          </label>

          <label>
            Target combined odds
            <input
              type="number"
              min={1.1}
              step={0.1}
              value={targetOdds}
              onChange={(e) => setTargetOdds(Number(e.target.value))}
            />
          </label>

          <label>
            League <span style={{ color: "var(--text-muted)", fontWeight: 400 }}>(optional)</span>
            <select value={league} onChange={(e) => setLeague(e.target.value)}>
              <option value="">Any league</option>
              {LEAGUES.map((l) => (
                <option key={l} value={l}>
                  {l}
                </option>
              ))}
            </select>
          </label>

          <label>
            Window
            <select value={daysAhead} onChange={(e) => setDaysAhead(Number(e.target.value))}>
              {[3, 7, 14].map((d) => (
                <option key={d} value={d}>
                  Next {d} days
                </option>
              ))}
            </select>
          </label>
        </div>

        <div style={{ marginTop: 14 }}>
          <div className="meta" style={{ marginBottom: 6 }}>
            Minimum accuracy per leg
          </div>
          <div className="filter-bar">
            {ACCURACY_OPTIONS.map((o) => (
              <button
                key={o.value}
                className={`filter-chip${minProbability === o.value ? " active" : ""}`}
                onClick={() => setMinProbability(o.value)}
              >
                {o.label}
              </button>
            ))}
          </div>
        </div>

        <div style={{ marginTop: 14 }}>
          <div className="meta" style={{ marginBottom: 6 }}>
            Markets <span style={{ fontWeight: 400 }}>(none selected = any market)</span>
          </div>
          <div className="filter-bar">
            {MARKET_OPTIONS.map((m) => (
              <button
                key={m.value}
                className={`filter-chip${markets.includes(m.value) ? " active" : ""}`}
                onClick={() => toggleMarket(m.value)}
              >
                {m.label}
              </button>
            ))}
          </div>
        </div>

        <button className="btn" style={{ marginTop: 16 }} onClick={handlePreview} disabled={previewing}>
          {previewing ? "Finding selections…" : "Preview selections"}
        </button>
      </div>

      {previewError && <ErrorState message={previewError} />}

      {preview && (
        <div className="card card-pad" style={{ marginBottom: 20 }}>
          <div className="section-header" style={{ marginBottom: 12 }}>
            <h3 style={{ margin: 0 }}>
              {preview.legs.length} leg{preview.legs.length === 1 ? "" : "s"}
              {preview.met_target ? "" : " (short of target)"}
            </h3>
            <span className="meta tabular-nums">
              {preview.combined_odds.toFixed(2)} combined · {(preview.combined_probability * 100).toFixed(0)}%
              combined probability
            </span>
          </div>

          {preview.legs.length === 0 ? (
            <EmptyState icon="◌" title="No matches qualify for these criteria." />
          ) : (
            <div className="predictions-table-wrapper">
              <table className="predictions-table">
                <thead>
                  <tr>
                    <th>Match</th>
                    <th>Market</th>
                    <th>Selection</th>
                    <th>Probability</th>
                    <th>Odds</th>
                    <th>Priced by</th>
                    <th>Kickoff</th>
                  </tr>
                </thead>
                <tbody>
                  {preview.legs.map((leg) => (
                    <tr key={`${leg.match_id}-${leg.market}-${leg.selection}`}>
                      <td>
                        <div className="match-cell">
                          {leg.home_team} vs {leg.away_team}
                        </div>
                        <div className="sub">{leg.league}</div>
                      </td>
                      <td className="sub">{leg.market}</td>
                      <td>
                        <strong>{leg.selection}</strong>
                      </td>
                      <td className="tabular-nums">{(leg.model_probability * 100).toFixed(0)}%</td>
                      <td className="tabular-nums">{leg.decimal_odds.toFixed(2)}</td>
                      <td className="sub">{leg.priced_by}</td>
                      <td className="sub">
                        {new Date(leg.kickoff).toLocaleString(undefined, {
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
          )}

          {preview.warnings.map((w, i) => (
            <p className="setting-note" style={{ marginTop: 10 }} key={i}>
              {w}
            </p>
          ))}

          {preview.legs.length > 0 && (
            <button className="btn" style={{ marginTop: 14 }} onClick={handleGenerate} disabled={generating}>
              {generating ? "Sending to " + bookmaker + "…" : `Generate ${bookmaker} booking code`}
            </button>
          )}
        </div>
      )}

      {generateError && <ErrorState message={generateError} />}

      {result && (
        <div className="card card-pad" style={{ marginBottom: 20 }}>
          <div className="section-header" style={{ marginBottom: 12 }}>
            <h3 style={{ margin: 0 }}>{statusLabel(result.status)}</h3>
            <span className="meta">{result.bookmaker}</span>
          </div>

          {result.status === "code_ready" && result.booking_code ? (
            <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
              <code style={{ fontSize: 20, fontWeight: 700, letterSpacing: 1 }}>{result.booking_code}</code>
              <CopyButton text={result.booking_code} label="Copy code" />
              {result.deep_link && (
                <a className="btn ghost" href={result.deep_link} target="_blank" rel="noreferrer">
                  Open in {result.bookmaker}
                </a>
              )}
            </div>
          ) : (
            <p className="setting-note" style={{ margin: 0 }}>
              {result.provider_message ??
                "This code could not be generated, but the selections and combined price above are real."}
            </p>
          )}

          <p className="meta" style={{ marginTop: 10 }}>
            Expires when the first leg kicks off:{" "}
            {new Date(result.expires_at).toLocaleString(undefined, {
              weekday: "short",
              month: "short",
              day: "numeric",
              hour: "2-digit",
              minute: "2-digit",
            })}
          </p>
        </div>
      )}

      {history && history.length > 0 && (
        <div className="card card-pad">
          <div className="section-header" style={{ marginBottom: 12 }}>
            <h3 style={{ margin: 0 }}>Your history</h3>
          </div>
          <div className="predictions-table-wrapper">
            <table className="predictions-table">
              <thead>
                <tr>
                  <th>Created</th>
                  <th>Bookmaker</th>
                  <th>Legs</th>
                  <th>Combined odds</th>
                  <th>Status</th>
                  <th>Code</th>
                </tr>
              </thead>
              <tbody>
                {history.map((slip) => (
                  <tr key={slip.id}>
                    <td className="sub">
                      {new Date(slip.created_at).toLocaleDateString(undefined, { month: "short", day: "numeric" })}
                    </td>
                    <td>{slip.bookmaker}</td>
                    <td className="tabular-nums">{slip.legs.length}</td>
                    <td className="tabular-nums">{slip.combined_odds.toFixed(2)}</td>
                    <td className="sub">{statusLabel(slip.status)}</td>
                    <td>{slip.booking_code ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
