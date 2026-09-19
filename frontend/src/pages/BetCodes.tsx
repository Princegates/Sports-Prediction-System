import { useState } from "react";
import { previewBetCode } from "../api";
import { CopyButton } from "../components/CopyButton";
import { EmptyState } from "../components/EmptyState";
import { ErrorState } from "../components/ErrorState";
import { LEAGUES } from "../components/AppShell";
import type { BetCodeCriteria, BetCodeLeg, BetCodePreview } from "../types";

/** Plain-text description of one generated combo, meant to be pasted
 * wherever the user places bets themselves. No bookmaker code, no deep
 * link -- see this file's module docstring for why. */
function formatLegsForCopy(legs: BetCodeLeg[], combinedOdds: number): string {
  const header = `AI Generation -- ${legs.length} leg combo, ${combinedOdds.toFixed(2)} combined odds (copied ${new Date().toLocaleString()})`;
  const lines = legs.map((leg) => {
    const kickoff = new Date(leg.kickoff).toLocaleString(undefined, {
      weekday: "short", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    });
    return (
      `${leg.home_team} vs ${leg.away_team} (${leg.league}, ${kickoff})\n` +
      `${leg.market}: ${leg.selection} -- ${(leg.model_probability * 100).toFixed(0)}% probability, ` +
      `${leg.decimal_odds.toFixed(2)} odds (${leg.priced_by})`
    );
  });
  return [header, "", ...lines].join("\n\n");
}

/**
 * The "AI Generation" step: builds a multi-match selection toward a target
 * combined price, priced entirely from real, stored bookmaker odds.
 *
 * There used to be a second step here that sent the result to a booking-code
 * aggregator and handed back a redeemable bookmaker code. It's gone: every
 * such service this project could find -- BetPaddi included -- only
 * converts a code that already exists on one bookmaker to another, never
 * mints a fresh one from a raw list of selections. That isn't a gap in this
 * integration, it's what the whole market actually offers; no aggregator
 * can place a bet on a bookmaker's platform on your behalf. So this page
 * shows exactly what it can stand behind: real matches, real markets, real
 * prices, and the combined number they add up to -- copyable, not a
 * fabricated code that would fail the moment someone tried to redeem it.
 */

const MARKET_OPTIONS = [
  { value: "Match Result", label: "Match result (1X2)" },
  { value: "Double Chance", label: "Double chance" },
  { value: "Both Teams To Score", label: "Both teams to score" },
  { value: "Draw No Bet", label: "Draw no bet" },
  { value: "Total Goals 1.5", label: "Over/Under 1.5 goals" },
  { value: "Total Goals 2.5", label: "Over/Under 2.5 goals" },
  { value: "Total Goals 3.5", label: "Over/Under 3.5 goals" },
  { value: "Correct Score", label: "Correct score" },
];

const ACCURACY_OPTIONS = [
  { value: 0.5, label: "50%+" },
  { value: 0.65, label: "65%+" },
  { value: 0.75, label: "75%+" },
  { value: 0.85, label: "85%+" },
];

export function BetCodes() {
  const [targetOdds, setTargetOdds] = useState(3.0);
  const [markets, setMarkets] = useState<string[]>([]);
  const [minProbability, setMinProbability] = useState(0.65);
  const [league, setLeague] = useState("");
  const [daysAhead, setDaysAhead] = useState(7);

  const [preview, setPreview] = useState<BetCodePreview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [previewing, setPreviewing] = useState(false);

  function criteria(): BetCodeCriteria {
    return {
      // Vestigial on the backend now that no aggregator is called -- kept
      // only because the API still accepts a bookmaker field on the
      // criteria payload; it has no bearing on which matches or prices
      // are found.
      bookmaker: "any",
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
    try {
      setPreview(await previewBetCode(criteria()));
    } catch (e) {
      setPreview(null);
      setPreviewError(String(e instanceof Error ? e.message : e));
    } finally {
      setPreviewing(false);
    }
  }

  function toggleMarket(value: string) {
    setMarkets((m) => (m.includes(value) ? m.filter((v) => v !== value) : [...m, value]));
  }

  return (
    <div>
      <div className="section-header">
        <h2>AI Generation</h2>
        <span className="meta">Combine matches toward a target price, priced from real bookmaker odds</span>
      </div>

      <p className="setting-note" style={{ marginBottom: 16 }}>
        Every leg here is priced from a real, stored bookmaker quote -- there is no estimated or synthetic
        price. Combining matches multiplies the risk as fast as it multiplies the price: three legs each
        70% likely land around a 34% chance of all three coming in, whatever the combined odds look like.
        The number below is calculated, not softened. No bookmaker booking code is generated -- paste the
        selections into your betting app yourself, or use the "Copy selections" button below.
      </p>

      <div className="card card-pad" style={{ marginBottom: 20 }}>
        <div className="auth-form" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 12 }}>
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
            Markets{" "}
            <span style={{ fontWeight: 400 }}>
              (none selected = match result, double chance, BTTS, draw no bet, and the three main goal lines)
            </span>
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
          {previewing ? "Finding selections…" : "Generate selections"}
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
            <div style={{ marginTop: 14 }}>
              <CopyButton text={formatLegsForCopy(preview.legs, preview.combined_odds)} label="Copy selections" />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
