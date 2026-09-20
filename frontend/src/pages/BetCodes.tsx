import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { createAdminPick, deleteAdminPick, fetchAdminPicksAdmin, previewBetCode, priceSelections } from "../api";
import { CopyButton } from "../components/CopyButton";
import { EmptyState } from "../components/EmptyState";
import { ErrorState } from "../components/ErrorState";
import { leagueLabel, useLeague } from "../components/AppShell";
import { useAuth } from "../lib/AuthContext";
import type { AdminPick, BetCodeCriteria, BetCodeLeg, BetCodePick, BetCodePreview } from "../types";

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

// Matches the backend's own default (SlipCriteria.max_legs / the
// betcode_max_legs setting) so leaving this untouched keeps prior behavior.
const DEFAULT_MAX_LEGS = 8;
const LEG_COUNT_OPTIONS = [2, 3, 4, 5, 6, 8, 10];

type RiskLevel = "low" | "medium" | "high";

/**
 * One-click generation presets. Each fills in (and immediately runs) the
 * same criteria fields the form below exposes -- these aren't a separate
 * mode, just a fast way to reach a sensible corner of the same search.
 *
 * The three levers that actually drive risk here: how safe the floor is,
 * how many legs get stacked (more legs compounds risk fast even at a high
 * floor), and which markets are in play. Low risk leaves markets at the
 * engine's own default set (DEFAULT_MARKETS on the backend -- meaningful
 * markets, no trivial extreme goal lines). High risk explicitly adds
 * Correct Score on top of that default set: it's this project's highest-
 * variance, highest-odds market, deliberately excluded from the default
 * search, and "higher odds accumulation" means actually reaching into it
 * rather than just lowering the floor on the same safe markets.
 */
const RISK_PRESETS: Record<
  RiskLevel,
  { label: string; description: string; minProbability: number; maxLegs: number; targetOdds: number; markets: string[] }
> = {
  low: {
    label: "Low risk",
    description: "85%+ picks only, up to 3 legs, the safest markets",
    minProbability: 0.85,
    maxLegs: 3,
    targetOdds: 3,
    markets: [],
  },
  medium: {
    label: "Medium risk",
    description: "65%+ picks, up to 5 legs",
    minProbability: 0.65,
    maxLegs: 5,
    targetOdds: 10,
    markets: [],
  },
  high: {
    label: "High risk",
    description: "50%+ picks, up to 8 legs, correct score included",
    minProbability: 0.5,
    maxLegs: 8,
    targetOdds: 50,
    markets: [...MARKET_OPTIONS.map((m) => m.value)],
  },
};

// Labels the *result*, independent of which preset (if any) produced it --
// a "High risk" generate that only found 2 very safe legs is honestly a
// low-risk result, and this says so rather than repeating the input choice.
function resultRiskLabel(combinedProbability: number): { label: string; tone: "low" | "medium" | "high" } {
  if (combinedProbability >= 0.5) return { label: "Low risk", tone: "low" };
  if (combinedProbability >= 0.2) return { label: "Medium risk", tone: "medium" };
  return { label: "High risk", tone: "high" };
}

export function BetCodes() {
  const { league } = useLeague();
  const { user } = useAuth();
  const location = useLocation();
  const [targetOdds, setTargetOdds] = useState(3.0);
  const [maxLegs, setMaxLegs] = useState(DEFAULT_MAX_LEGS);
  const [markets, setMarkets] = useState<string[]>([]);
  const [minProbability, setMinProbability] = useState(0.65);
  const [daysAhead, setDaysAhead] = useState(7);
  // Which risk preset (if any) exactly matches the form's current values --
  // cleared the moment any control is touched by hand, so the highlighted
  // chip never claims a match that no longer holds.
  const [activeRisk, setActiveRisk] = useState<RiskLevel | null>(null);

  const [preview, setPreview] = useState<BetCodePreview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [previewing, setPreviewing] = useState(false);
  // Set only when the current preview came from picks handed over by Guda
  // (or another page) rather than the criteria form below -- changes the
  // result card's framing, not how it's priced.
  const [fromExternalPicks, setFromExternalPicks] = useState(false);

  // Superadmin-only: promoting the current preview as an "Admin Pick" onto
  // every Dashboard (distinct from Guda Picks' single-outcome promotion).
  const [adminPicks, setAdminPicks] = useState<AdminPick[]>([]);
  const [featuring, setFeaturing] = useState(false);
  const [pickBusy, setPickBusy] = useState<number | null>(null);

  useEffect(() => {
    if (user?.role !== "superadmin") return;
    let cancelled = false;
    fetchAdminPicksAdmin()
      .then((picks) => !cancelled && setAdminPicks(picks))
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [user?.role]);

  function criteria(overrides?: Partial<BetCodeCriteria>): BetCodeCriteria {
    return {
      // Vestigial on the backend now that no aggregator is called -- kept
      // only because the API still accepts a bookmaker field on the
      // criteria payload; it has no bearing on which matches or prices
      // are found.
      bookmaker: "any",
      target_odds: targetOdds,
      max_legs: maxLegs,
      markets,
      min_probability: minProbability,
      league: league || null,
      days_ahead: daysAhead,
      ...overrides,
    };
  }

  async function runPreview(fetchPreview: () => Promise<BetCodePreview>) {
    setPreviewing(true);
    setPreviewError(null);
    try {
      setPreview(await fetchPreview());
    } catch (e) {
      setPreview(null);
      setPreviewError(String(e instanceof Error ? e.message : e));
    } finally {
      setPreviewing(false);
    }
  }

  // A pick list handed over via navigation (Guda's "Send to AI Generation",
  // or any future source) prices immediately on arrival -- once per
  // navigation, not on every re-render.
  useEffect(() => {
    const picks = (location.state as { picks?: BetCodePick[] } | null)?.picks;
    if (!picks || picks.length === 0) return;
    setFromExternalPicks(true);
    runPreview(() => priceSelections(picks));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.state]);

  function handlePreview() {
    setFromExternalPicks(false);
    return runPreview(() => previewBetCode(criteria()));
  }

  // Fills in the preset's values (so the form reflects what actually ran,
  // and stays tweakable afterward) and generates immediately -- one click
  // for "just get me a low-risk slip" rather than four.
  function runPreset(risk: RiskLevel) {
    const p = RISK_PRESETS[risk];
    setTargetOdds(p.targetOdds);
    setMaxLegs(p.maxLegs);
    setMinProbability(p.minProbability);
    setMarkets(p.markets);
    setActiveRisk(risk);
    setFromExternalPicks(false);
    return runPreview(() =>
      previewBetCode(criteria({ target_odds: p.targetOdds, max_legs: p.maxLegs, min_probability: p.minProbability, markets: p.markets })),
    );
  }

  function toggleMarket(value: string) {
    setActiveRisk(null);
    setMarkets((m) => (m.includes(value) ? m.filter((v) => v !== value) : [...m, value]));
  }

  // Promotes the current preview's legs onto every Dashboard's Admin Picks
  // section. Legs are re-priced from scratch server-side, so what actually
  // gets featured is always the current real price, not whatever the
  // browser last saw -- same reasoning as MatchDetail's Guda Pick toggle.
  async function handleFeatureSlip() {
    if (!preview || preview.legs.length === 0) return;
    const label = window.prompt("Optional label for this slip (e.g. \"Weekend Banker\") -- Cancel to skip featuring it:", "");
    if (label === null) return;
    const note = window.prompt("Optional note (shown on every Dashboard):", "") ?? undefined;
    setFeaturing(true);
    try {
      const created = await createAdminPick({
        legs: preview.legs.map((l) => ({ match_id: l.match_id, market: l.market, selection: l.selection })),
        label: label || undefined,
        note: note || undefined,
      });
      setAdminPicks((prev) => [created, ...prev]);
    } catch (err) {
      window.alert(String(err instanceof Error ? err.message : err));
    } finally {
      setFeaturing(false);
    }
  }

  async function handleRemoveAdminPick(pickId: number) {
    setPickBusy(pickId);
    try {
      await deleteAdminPick(pickId);
      setAdminPicks((prev) => prev.filter((p) => p.id !== pickId));
    } catch (err) {
      window.alert(String(err instanceof Error ? err.message : err));
    } finally {
      setPickBusy(null);
    }
  }

  return (
    <div>
      <div className="section-header">
        <h2>AI Generation</h2>
        <span className="meta">
          Combine matches toward a target price, priced from real bookmaker odds -- scoped to{" "}
          <strong>{leagueLabel(league)}</strong> (change league in the top bar)
        </span>
      </div>

      <p className="setting-note" style={{ marginBottom: 16 }}>
        Every leg here is priced from a real, stored bookmaker quote -- there is no estimated or synthetic
        price. Combining matches multiplies the risk as fast as it multiplies the price: three legs each
        70% likely land around a 34% chance of all three coming in, whatever the combined odds look like.
        The number below is calculated, not softened. No bookmaker booking code is generated -- paste the
        selections into your betting app yourself, or use the "Copy selections" button below.
      </p>

      <div className="card card-pad" style={{ marginBottom: 20 }}>
        <div className="meta" style={{ marginBottom: 10 }}>
          Generate by risk level
        </div>
        <div className="risk-preset-row">
          {(Object.keys(RISK_PRESETS) as RiskLevel[]).map((risk) => {
            const p = RISK_PRESETS[risk];
            return (
              <button
                key={risk}
                type="button"
                className={`risk-preset-card ${risk}${activeRisk === risk ? " active" : ""}`}
                onClick={() => runPreset(risk)}
                disabled={previewing}
              >
                <span className="risk-preset-label">{p.label}</span>
                <span className="risk-preset-description">{p.description}</span>
              </button>
            );
          })}
        </div>
        <p className="setting-note" style={{ marginTop: 10, marginBottom: 0 }}>
          Fills in (and runs) the fields below -- feel free to adjust anything afterward and generate again.
        </p>
      </div>

      <div className="card card-pad" style={{ marginBottom: 20 }}>
        <div className="auth-form" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 12 }}>
          <label>
            Target combined odds
            <input
              type="number"
              min={1.1}
              step={0.1}
              value={targetOdds}
              onChange={(e) => {
                setActiveRisk(null);
                setTargetOdds(Number(e.target.value));
              }}
            />
          </label>

          <label>
            Window
            <select
              value={daysAhead}
              onChange={(e) => {
                setActiveRisk(null);
                setDaysAhead(Number(e.target.value));
              }}
            >
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
            Number of legs{" "}
            <span style={{ fontWeight: 400 }}>
              (selection stops at whichever comes first: this many legs, or the target odds above)
            </span>
          </div>
          <div className="filter-bar">
            {LEG_COUNT_OPTIONS.map((n) => (
              <button
                key={n}
                className={`filter-chip${maxLegs === n ? " active" : ""}`}
                onClick={() => {
                  setActiveRisk(null);
                  setMaxLegs(n);
                }}
              >
                {n}
              </button>
            ))}
          </div>
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
                onClick={() => {
                  setActiveRisk(null);
                  setMinProbability(o.value);
                }}
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
          {fromExternalPicks && (
            <p className="setting-note" style={{ marginBottom: 12 }}>
              Priced from Guda's picks -- same real, stored bookmaker quotes as everything else on this
              page, just not run through the criteria form below.
            </p>
          )}
          <div className="section-header" style={{ marginBottom: 12 }}>
            <h3 style={{ margin: 0 }}>
              {preview.legs.length} leg{preview.legs.length === 1 ? "" : "s"}
              {/* Stopping at the leg count someone asked for isn't a
                  shortfall -- only flag "short of target" when it fell short
                  of *both* stopping conditions, i.e. ran out of qualifying
                  matches before reaching either one. */}
              {!preview.met_target && !(preview.legs.length > 0 && preview.legs.length === maxLegs) ? " (short of target)" : ""}
            </h3>
            {preview.legs.length > 0 && (
              <span className="meta tabular-nums" style={{ display: "flex", alignItems: "center", gap: 10 }}>
                {preview.combined_odds.toFixed(2)} combined · {(preview.combined_probability * 100).toFixed(0)}%
                combined probability
                <span className={`risk-tag ${resultRiskLabel(preview.combined_probability).tone}`}>
                  {resultRiskLabel(preview.combined_probability).label}
                </span>
              </span>
            )}
          </div>

          {preview.legs.length === 0 ? (
            <EmptyState
              icon="◌"
              title={
                fromExternalPicks
                  ? "None of those picks have a real, stored bookmaker price -- see the warnings below."
                  : "No matches qualify for these criteria."
              }
            />
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
            <div style={{ marginTop: 14, display: "flex", gap: 10, flexWrap: "wrap" }}>
              <CopyButton text={formatLegsForCopy(preview.legs, preview.combined_odds)} label="Copy selections" />
              {user?.role === "superadmin" && (
                <button className="btn ghost" onClick={handleFeatureSlip} disabled={featuring}>
                  {featuring ? "Featuring…" : "★ Feature this slip"}
                </button>
              )}
            </div>
          )}
        </div>
      )}

      {user?.role === "superadmin" && adminPicks.length > 0 && (
        <div className="card card-pad" style={{ marginTop: 20 }}>
          <div className="section-header" style={{ marginBottom: 10 }}>
            <h3 style={{ margin: 0 }}>Currently featured Admin Picks</h3>
            <span className="meta">Live on every Dashboard right now</span>
          </div>
          {adminPicks.map((pick) => (
            <div
              key={pick.id}
              className="match-meta-row"
              style={{ justifyContent: "space-between", padding: "8px 0", borderTop: "1px solid var(--border)" }}
            >
              <span>
                {pick.label ? <strong>{pick.label}</strong> : <span className="sub">Untitled slip</span>}{" "}
                <span className="sub">
                  {pick.legs.length} leg{pick.legs.length === 1 ? "" : "s"} · {pick.combined_odds.toFixed(2)} odds ·{" "}
                  {(pick.combined_probability * 100).toFixed(0)}% probability
                </span>
                <span className={`risk-tag ${pick.risk_tier}`} style={{ marginLeft: 8 }}>
                  {pick.risk_tier === "low" ? "Low risk" : pick.risk_tier === "medium" ? "Medium risk" : "High risk"}
                </span>
              </span>
              <button
                className="btn ghost"
                style={{ padding: "2px 8px", fontSize: 12 }}
                disabled={pickBusy === pick.id}
                onClick={() => handleRemoveAdminPick(pick.id)}
              >
                Remove
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
