import type { Prediction } from "../types";

// The same three-factor bar as the backend's is_high_confidence() (spec
// section 42's "High-Confidence Prediction Mode") -- a probability this high
// only counts as a "hot pick" alongside enough match history and models that
// agree with each other, so a thin-data or split-model 90% doesn't earn the
// same badge as a well-supported one.
export function isHotPick(probability: number, dataQuality: number, modelAgreement: number): boolean {
  return probability >= 0.9 && dataQuality >= 0.9 && modelAgreement >= 0.85;
}

export function isHighConfidence(p: Prediction): boolean {
  return isHotPick(p.global_outcome.probability, p.data_quality_score, p.model_agreement_score);
}

export function dateOffset(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}
