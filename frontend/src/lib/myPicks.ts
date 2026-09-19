import type { BettingOutcome } from "../types";

const STORAGE_KEY = "market_picks";

/**
 * A manually-built shortlist of outcomes picked while browsing the Markets
 * page -- a per-browser scratch pad, not an account-level feature (there is
 * no server-side "my picks" table; it never leaves this device). Deliberately
 * probability-only: the Markets browse response carries no real bookmaker
 * price, and this project never shows a combined price that isn't backed by
 * one -- see pages/BetCodes.tsx (AI Generation) for the priced, algorithmic
 * way to build a combo instead.
 *
 * At most one pick per match, same reasoning as AI Generation's own
 * one-leg-per-match rule: two outcomes from the same fixture are correlated,
 * not independent, so multiplying their probabilities into a combined number
 * would overstate it.
 */

export function readStoredPicks(): BettingOutcome[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export function storePicks(picks: BettingOutcome[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(picks));
  } catch {
    // private-browsing / storage-disabled -- picks just won't persist
  }
}

/** Plain-text description meant to be pasted wherever the user places bets
 * themselves. */
export function formatPicksForCopy(picks: BettingOutcome[]): string {
  const combined = picks.reduce((p, o) => p * o.probability, 1);
  const header =
    `My picks -- ${picks.length} selection${picks.length === 1 ? "" : "s"}, ` +
    `${(combined * 100).toFixed(0)}% combined probability (copied ${new Date().toLocaleString()})`;
  const lines = picks.map((o) => {
    const kickoff = new Date(o.kickoff).toLocaleString(undefined, {
      weekday: "short", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    });
    return (
      `${o.home_team} vs ${o.away_team} (${o.league}, ${kickoff})\n` +
      `${o.market}: ${o.selection} -- ${(o.probability * 100).toFixed(0)}% probability`
    );
  });
  return [header, "", ...lines].join("\n\n");
}
