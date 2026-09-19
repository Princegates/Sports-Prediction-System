import type { MatchSummary, Prediction } from "../types";

/**
 * Plain-text description of the AI's call for one match, meant to be pasted
 * wherever a user tracks their own picks or places bets themselves. Just a
 * probability statement -- no bookmaker integration, no code, no deep link.
 * That combination lives in the booking-codes feature (see app/betcode/ on
 * the backend and pages/BetCodes.tsx here) for someone who wants it; this
 * function is for someone who doesn't, and stays that thin on purpose.
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
