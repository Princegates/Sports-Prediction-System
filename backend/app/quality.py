"""Data-quality and confidence scoring (spec sections 33-35)."""

from __future__ import annotations


def data_quality_score(matches_home: int, matches_away: int, min_matches: int = 10) -> float:
    """0..1 completeness score. Widen this later with API-reliability /
    news-reliability / lineup-certainty factors as those sources are added --
    the signature is intentionally forward-compatible (extra kwargs can be
    added without breaking callers that only pass match counts)."""

    if min_matches <= 0:
        return 1.0
    completeness = min(matches_home, matches_away) / min_matches
    return round(max(0.0, min(1.0, completeness)), 3)


def confidence_label(data_quality: float, model_agreement: float) -> str:
    if data_quality >= 0.85 and model_agreement >= 0.80:
        return "HIGH"
    if data_quality >= 0.6 and model_agreement >= 0.6:
        return "MEDIUM"
    return "LOW"


def is_high_confidence(probability: float, data_quality: float, model_agreement: float) -> bool:
    """Spec section 42's 'High-Confidence Prediction Mode' thresholds."""

    return probability >= 0.90 and data_quality >= 0.90 and model_agreement >= 0.85
