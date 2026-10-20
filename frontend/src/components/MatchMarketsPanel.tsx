import { useEffect, useState } from "react";
import { fetchMatchOutcomes } from "../api";
import type { BettingOutcome, OutcomesResponse } from "../types";
import { marketLabel } from "../lib/marketLabel";

/** The markets most people bet, shown first; everything else is one click
 * away under "Show every market". */
const MAIN_MARKETS = [
  "Match Result",
  "Double Chance",
  "Draw No Bet",
  "Both Teams To Score",
  "Total Goals 1.5",
  "Total Goals 2.5",
  "Total Goals 3.5",
];

// One fetch per match per page visit, shared between re-opens.
const cache = new Map<number, Promise<OutcomesResponse>>();

function load(matchId: number): Promise<OutcomesResponse> {
  let p = cache.get(matchId);
  if (!p) {
    p = fetchMatchOutcomes(matchId);
    p.catch(() => cache.delete(matchId));
    cache.set(matchId, p);
  }
  return p;
}

export interface ChosenOutcome {
  market: string;
  selection: string;
  probability: number;
}

/**
 * Every market the model prices for one match, with its probability, so a
 * member can pick the outcome they actually want instead of the single one
 * the system ranked highest. Choosing one hands it back via onChoose;
 * choosing it again clears the choice (back to the AI pick).
 */
export function MatchMarketsPanel({
  matchId,
  chosen,
  onChoose,
}: {
  matchId: number;
  chosen: ChosenOutcome | null;
  onChoose: (outcome: ChosenOutcome | null) => void;
}) {
  const [data, setData] = useState<OutcomesResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);

  useEffect(() => {
    let cancelled = false;
    load(matchId)
      .then((d) => !cancelled && setData(d))
      .catch((err) => !cancelled && setError(err instanceof Error ? err.message : String(err)));
    return () => {
      cancelled = true;
    };
  }, [matchId]);

  if (error) return <p className="sub">Couldn't load this match's markets -- {error}</p>;
  if (!data) return <p className="sub">Loading every market…</p>;

  const outcomes = data.leagues[0]?.outcomes ?? [];
  const byMarket = new Map<string, BettingOutcome[]>();
  for (const o of outcomes) {
    const list = byMarket.get(o.market) ?? [];
    list.push(o);
    byMarket.set(o.market, list);
  }
  const main = MAIN_MARKETS.filter((m) => byMarket.has(m));
  const rest = [...byMarket.keys()].filter((m) => !MAIN_MARKETS.includes(m));
  const markets = showAll ? [...main, ...rest] : main;

  if (byMarket.size === 0) return <p className="sub">Not enough history yet to price markets for this match.</p>;

  return (
    <div className="markets-panel">
      <p className="sub" style={{ margin: 0 }}>
        Tap any outcome to use it as this match's pick instead of the AI's. It's what goes to AI Generation.
      </p>
      {markets.map((market) => (
        <div key={market} className="markets-panel-market">
          <span className="markets-panel-name">{marketLabel(market)}</span>
          <div className="markets-panel-options">
            {[...(byMarket.get(market) ?? [])]
              .sort((a, b) => b.probability - a.probability)
              .map((o) => {
                const isChosen = chosen?.market === o.market && chosen?.selection === o.selection;
                return (
                  <button
                    key={o.selection}
                    type="button"
                    title={o.definition}
                    className={`markets-panel-option${isChosen ? " chosen" : ""}`}
                    aria-pressed={isChosen}
                    onClick={() =>
                      onChoose(isChosen ? null : { market: o.market, selection: o.selection, probability: o.probability })
                    }
                  >
                    <span>{o.selection}</span>
                    <strong className="tabular-nums">{Math.round(o.probability * 100)}%</strong>
                  </button>
                );
              })}
          </div>
        </div>
      ))}
      {rest.length > 0 && (
        <button type="button" className="btn ghost btn-sm" onClick={() => setShowAll((v) => !v)}>
          {showAll ? "Show main markets only" : `Show every market (${rest.length} more)`}
        </button>
      )}
    </div>
  );
}
