import { Link } from "react-router-dom";
import { chanceLabel } from "../lib/odds";

/**
 * Heads-ups for risky AI Generation slips. They only inform: nothing here
 * disables a button, asks "are you sure?" or hides a result. A person who
 * understands the odds can carry on exactly as before.
 */

/** Below this combined chance a slip counts as high risk -- the same line
 * the result's own "High risk" tag uses (see BetCodes.resultRiskLabel). */
export const RISKY_BELOW = 0.2;
/** Below this, the wording gets stronger. */
export const VERY_RISKY_BELOW = 0.05;
/** Before generating we only know the worst case (every game at the lowest
 * accuracy allowed), so only really aggressive settings trigger a notice;
 * the normal defaults don't, or the warning would be background noise. */
const SETTINGS_WARN_BELOW = 0.02;

function Notice({ tone, title, children }: { tone: "caution" | "high"; title: string; children: React.ReactNode }) {
  return (
    <div className={`risk-notice ${tone}`} role="status">
      <strong>{title}</strong>
      <p>{children}</p>
    </div>
  );
}

/** Shown with a generated slip, whenever its chance of winning is low. It
 * follows the slip as legs are removed, so it goes away if the slip stops
 * being a long shot. */
export function ResultRiskNotice({ legCount, probability }: { legCount: number; probability: number }) {
  if (legCount === 0 || probability >= RISKY_BELOW) return null;
  const veryRisky = probability < VERY_RISKY_BELOW;
  return (
    <Notice tone={veryRisky ? "high" : "caution"} title={veryRisky ? "Very risky slip" : "Risky slip"}>
      For this slip to win, all {legCount} game{legCount === 1 ? "" : "s"} must win. We put the chance at{" "}
      <strong>{chanceLabel(probability)}</strong>, so {veryRisky ? "most slips like this lose" : "it will lose more often than it wins"}.
      You can still use it. Only stake money you can afford to lose.{" "}
      <Link to="/responsible">Read about playing responsibly</Link>
    </Notice>
  );
}

/** Shown before generating, when the settings alone make a long shot likely:
 * many games, each allowed to be only a little better than a coin flip. */
export function SettingsRiskNotice({ maxLegs, minProbability }: { maxLegs: number; minProbability: number }) {
  const floor = Math.pow(minProbability, maxLegs);
  if (!(floor < SETTINGS_WARN_BELOW)) return null;
  return (
    <Notice tone="caution" title="These settings can give a very risky slip">
      With up to {maxLegs} games that are each only {Math.round(minProbability * 100)}% likely, the chance that every
      game wins could be as low as <strong>{chanceLabel(floor)}</strong>. You can still generate it. Fewer games or a
      higher minimum accuracy make a slip safer.
    </Notice>
  );
}
