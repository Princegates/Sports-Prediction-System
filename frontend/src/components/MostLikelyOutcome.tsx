import type { GlobalOutcome } from "../types";
import { isHotPick } from "../lib/filters";
import { HotPickBadge } from "./HotPickBadge";

interface Props {
  outcome: GlobalOutcome;
  confidence: "HIGH" | "MEDIUM" | "LOW";
  dataQuality?: number;
  modelAgreement?: number;
}

export function MostLikelyOutcome({ outcome, confidence, dataQuality, modelAgreement }: Props) {
  if (outcome.market === "Insufficient Data") {
    return (
      <div className="outcome-hero low">
        <span className="star" aria-hidden>
          ⚠
        </span>
        <div className="body">
          <div className="eyebrow">Most likely outcome</div>
          <div className="selection">Not enough match history yet</div>
        </div>
      </div>
    );
  }

  const hot = dataQuality !== undefined && modelAgreement !== undefined && isHotPick(outcome.probability, dataQuality, modelAgreement);

  return (
    <div className={`outcome-hero ${confidence.toLowerCase()}`}>
      <span className="star" aria-hidden>
        ★
      </span>
      <div className="body">
        <div className="eyebrow">
          Most likely outcome &middot; {outcome.market}
        </div>
        <div className="selection">
          {outcome.selection}
          {hot && (
            <span style={{ marginLeft: 8 }}>
              <HotPickBadge />
            </span>
          )}
        </div>
      </div>
      <div className="prob tabular-nums">{Math.round(outcome.probability * 100)}%</div>
    </div>
  );
}

export function ConfidenceTag({ confidence }: { confidence: "HIGH" | "MEDIUM" | "LOW" }) {
  return <span className={`confidence-tag ${confidence.toLowerCase()}`}>{confidence} confidence</span>;
}
