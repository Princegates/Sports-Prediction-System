"""Outcome Registry (spec section 27).

The Global Outcome Engine must not blindly compare probabilities that
represent fundamentally different event definitions. Concretely: Home Win /
Draw / Away Win are mutually exclusive (they must sum to ~1), but Home Win /
Over 2.5 Goals / BTTS Yes are not (all three can happen in the same match).

Each ``Outcome`` records which ``mutually_exclusive_group`` it belongs to so
downstream consumers (e.g. a parlay/combo builder, or a sanity check that a
group's members sum to ~1) can reason about it correctly. The Global
Most-Likely Outcome Engine itself is intentionally allowed to compare across
groups -- that cross-market comparison is the entire point of spec section
65 ("Global Probability Intelligence").
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Outcome:
    market: str
    selection: str
    probability: float
    mutually_exclusive_group: str
    min_data_requirement: int
    definition: str


def build_outcome_registry(
    home_win: float,
    draw: float,
    away_win: float,
    over_probabilities: dict[str, float],
    btts_yes: float,
    btts_no: float,
    correct_score_probabilities: dict[str, float],
    matches_available: int,
) -> list[Outcome]:
    outcomes: list[Outcome] = [
        Outcome("Match Result", "Home Win", home_win, "1x2", 5, "Home team scores more goals than the away team over 90 minutes plus stoppage time."),
        Outcome("Match Result", "Draw", draw, "1x2", 5, "Both teams score the same number of goals over 90 minutes plus stoppage time."),
        Outcome("Match Result", "Away Win", away_win, "1x2", 5, "Away team scores more goals than the home team over 90 minutes plus stoppage time."),
        Outcome("Double Chance", "Home/Draw", home_win + draw, "double_chance", 5, "Home team wins or the match is drawn."),
        Outcome("Double Chance", "Home/Away", home_win + away_win, "double_chance", 5, "Either team wins (no draw)."),
        Outcome("Double Chance", "Draw/Away", draw + away_win, "double_chance", 5, "Match is drawn or the away team wins."),
        Outcome("Both Teams To Score", "Yes", btts_yes, "btts", 5, "Both teams score at least one goal."),
        Outcome("Both Teams To Score", "No", btts_no, "btts", 5, "At least one team fails to score."),
    ]

    for line, over_p in over_probabilities.items():
        outcomes.append(
            Outcome(f"Total Goals {line}", f"Over {line}", over_p, f"ou_{line}", 5, f"Total match goals are greater than {line}.")
        )
        outcomes.append(
            Outcome(f"Total Goals {line}", f"Under {line}", 1 - over_p, f"ou_{line}", 5, f"Total match goals are less than {line}.")
        )

    for score, prob in correct_score_probabilities.items():
        outcomes.append(
            Outcome("Correct Score", score, prob, "correct_score", 10, f"Final score is exactly {score}.")
        )

    return [o for o in outcomes if matches_available >= o.min_data_requirement]


def outcomes_from_prediction(prediction) -> list[Outcome]:
    """Rebuild the full registry from a stored ``Prediction`` row.

    Only the blended probabilities are persisted, not the outcome list they
    expand into -- storing both would let them disagree. Rebuilding is pure
    arithmetic over columns already loaded, so a page showing every market for
    fifty matches costs no extra queries.

    ``matches_available`` is recovered from ``data_quality_score``, which is
    ``min(home_matches, away_matches) / 10`` clamped to 1. That inverts exactly
    over the range the registry cares about, since the largest
    ``min_data_requirement`` is 10 -- so a score of 1.0 means "at least ten",
    which satisfies every outcome, and anything lower reconstructs the real
    count. Correct-score outcomes stay hidden for thin data exactly as they
    were when the prediction was generated.
    """

    return build_outcome_registry(
        prediction.home_win,
        prediction.draw,
        prediction.away_win,
        prediction.over_probabilities or {},
        prediction.btts_yes,
        prediction.btts_no,
        prediction.correct_score_probabilities or {},
        matches_available=round((prediction.data_quality_score or 0.0) * 10),
    )
