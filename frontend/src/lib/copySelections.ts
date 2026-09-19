import type { MatchSummary, Prediction } from "../types";

/**
 * Plain-text description of the AI's call for one match, meant to be pasted
 * wherever a user tracks their own picks or places bets themselves. This is
 * deliberately just a probability statement -- no bookmaker integration, no
 * generated bet-slip code, no deep link to stake. The platform's role stops
 * at analysis; what a user does with it happens entirely elsewhere, the same
 * way payment for access happens entirely outside this system.
 */
export function formatSelection(match: MatchSummary, prediction: Prediction): string {
  const kickoff = new Date(match.date).toLocaleString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
  const pct = (prediction.global_outcome.probability * 100).toFixed(0);
  return (
    `${match.home_team.name} vs ${match.away_team.name} (${match.league}, ${kickoff})\n` +
    `AI pick: ${prediction.global_outcome.selection} -- ${pct}% probability, ${prediction.confidence} confidence`
  );
}

export function formatSelections(rows: { match: MatchSummary; prediction: Prediction }[]): string {
  const header = `AI predictions -- probabilities, not guarantees (copied ${new Date().toLocaleString()})`;
  return [header, "", ...rows.map((r) => formatSelection(r.match, r.prediction))].join("\n\n");
}

/**
 * Wraps the Clipboard API, which can throw in an insecure context, an older
 * browser, or when the page lacks focus -- callers show their own
 * success/failure feedback from the returned boolean rather than assuming.
 */
export async function copyToClipboard(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}
