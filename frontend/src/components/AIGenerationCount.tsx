import { runsLeftOf, useAIGenerationQuota } from "../lib/aiQuota";

/** A tiny "runs left" count for the AI Generation navigation entries. Shows
 * nothing when there is no limit to count down (or it isn't known yet). */
export function AIGenerationCount({ onIcon = false }: { onIcon?: boolean }) {
  const left = runsLeftOf(useAIGenerationQuota());
  if (left === null) return null;
  const tone = left === 0 ? "empty" : left <= 2 ? "low" : "";
  return (
    <span
      className={`nav-count ${tone}${onIcon ? " on-icon" : ""}`}
      title={`${left} AI Generation run${left === 1 ? "" : "s"} left`}
      aria-label={`${left} runs left`}
    >
      {left}
    </span>
  );
}
