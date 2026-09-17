import type { Prediction } from "../types";

export function isHighConfidence(p: Prediction): boolean {
  return p.global_outcome.probability >= 0.9 && p.data_quality_score >= 0.9 && p.model_agreement_score >= 0.85;
}

export function dateOffset(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}
