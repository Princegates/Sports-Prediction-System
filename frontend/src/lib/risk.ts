import type { AdminPick } from "../types";

export type RiskTone = "low" | "medium" | "high";

export const RISK_LABELS: Record<RiskTone, string> = {
  low: "Low risk",
  medium: "Medium risk",
  high: "High risk",
};

/** The one risk scale, used everywhere a whole slip is tagged: it goes by the
 * chance of every game on the slip winning (the same thresholds as the
 * backend's betcode.selection.risk_tier), never by how likely each game is on
 * its own. Ten games at 85% each is a 20% slip, and is tagged as one. */
export function riskOf(combinedProbability: number): { label: string; tone: RiskTone } {
  const tone: RiskTone = combinedProbability >= 0.5 ? "low" : combinedProbability >= 0.2 ? "medium" : "high";
  return { label: RISK_LABELS[tone], tone };
}

// What each system accumulator tier really is: how likely each game on it is
// (see app.risk_picks). Said in those words rather than "Low/Medium/High
// risk", because a 10-15 game slip is a long shot at every tier.
const GAME_TIER: Record<string, string> = {
  low: "85%+ games",
  medium: "70–85% games",
  high: "55–70% games",
};

/** The title for a system daily/weekly accumulator ("85%+ games · Daily
 * slip"), or null for any other slip, which keeps its own label. */
export function systemSlipTitle(pick: Pick<AdminPick, "source">): string | null {
  const match = /^system_(daily|weekly)_(low|medium|high)$/.exec(pick.source ?? "");
  if (!match) return null;
  return `${GAME_TIER[match[2]]} · ${match[1] === "daily" ? "Daily" : "Weekly"} slip`;
}
