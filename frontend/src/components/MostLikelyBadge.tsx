import type { GlobalOutcome } from "../types";

interface Props {
  outcome: GlobalOutcome;
  confidence: "HIGH" | "MEDIUM" | "LOW";
}

export function MostLikelyBadge({ outcome, confidence }: Props) {
  return (
    <div className={`badge ${confidence.toLowerCase()}`}>
      <span className="star">★</span>
      <span>
        Most likely outcome: <span className="outcome">{outcome.selection}</span>
      </span>
      <span className="prob">{Math.round(outcome.probability * 100)}%</span>
    </div>
  );
}

export function ConfidenceTag({ confidence }: { confidence: "HIGH" | "MEDIUM" | "LOW" }) {
  return <span className={`confidence-tag ${confidence.toLowerCase()}`}>{confidence} confidence</span>;
}
