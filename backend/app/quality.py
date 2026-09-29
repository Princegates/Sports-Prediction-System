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


# Match-result probability the top pick needs for each band. Chosen on the
# validation slices of six leagues and checked on the test slices after them,
# where the bands resolved at:
#
#     LOW     top pick < 45%     ~40% of top picks correct   (~37% of matches)
#     MEDIUM  45% to 60%         ~50%                        (~43%)
#     HIGH    60% and up         ~70%                        (~20%)
#
# The same figures on validation and test, so they are not an accident of one
# slice.
HIGH_CONFIDENCE_PROBABILITY = 0.60
MEDIUM_CONFIDENCE_PROBABILITY = 0.45


def confidence_label(data_quality: float, model_agreement: float, top_probability: float | None = None) -> str:
    """HIGH / MEDIUM / LOW for a prediction, where ``top_probability`` is the
    highest of the three match-result probabilities.

    The label used to rest on data quality and model agreement alone. Measured
    on 3,717 held-out matches, agreement carries no information about whether
    the pick comes in -- the top pick was right ~50% of the time in every
    agreement tercile, and 98% of matches cleared the HIGH bar -- so nearly
    every mid-season fixture read HIGH and HIGH meant a coin flip between two
    outcomes. The blended probability does carry it (see the thresholds
    above), and it is what the public track record grades each band on.

    Data quality and agreement stay as gates: a strong-looking number on a
    thin history, or one the models split over, is still not HIGH. Without a
    ``top_probability`` the old two-factor label is returned unchanged.
    """

    if top_probability is None:
        if data_quality >= 0.85 and model_agreement >= 0.80:
            return "HIGH"
        if data_quality >= 0.6 and model_agreement >= 0.6:
            return "MEDIUM"
        return "LOW"

    if data_quality >= 0.85 and model_agreement >= 0.80 and top_probability >= HIGH_CONFIDENCE_PROBABILITY:
        return "HIGH"
    if data_quality >= 0.6 and model_agreement >= 0.6 and top_probability >= MEDIUM_CONFIDENCE_PROBABILITY:
        return "MEDIUM"
    return "LOW"


def is_high_confidence(probability: float, data_quality: float, model_agreement: float) -> bool:
    """Spec section 42's 'High-Confidence Prediction Mode' thresholds."""

    return probability >= 0.90 and data_quality >= 0.90 and model_agreement >= 0.85
