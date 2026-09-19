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

from app.prediction_models.poisson_model import build_score_matrix


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


# Extra goal-total lines and per-team lines beyond what build_outcome_registry
# already carries from the persisted ``over_probabilities`` column.
_EXTRA_TOTAL_LINES = ("5.5", "6.5", "7.5")
_TEAM_GOAL_LINES = ("0.5", "1.5", "2.5", "3.5", "4.5")


def matrix_derived_outcomes(lambda_home: float, lambda_away: float, matches_available: int) -> list[Outcome]:
    """Every outcome that is pure arithmetic over the model's own scoreline
    matrix -- no new statistics collected, no new model fit, just a different
    slice of a distribution the Poisson component already computes for
    Correct Score.

    Deliberately does not attempt half-time markets (no first-half/second-half
    split is modelled), cards/corners/fouls (no predictive model exists for
    them, though the raw historical counts are stored), player markets (no
    player-level data source), or goal-timing / first-to-score markets (no
    minute-level event model). Inventing numbers for those would look like
    analysis and be a guess.
    """

    matrix = build_score_matrix(lambda_home, lambda_away)
    max_goals = len(matrix) - 1

    home_goals = [sum(matrix[h]) for h in range(max_goals + 1)]
    away_goals = [sum(matrix[h][a] for h in range(max_goals + 1)) for a in range(max_goals + 1)]

    def total_over(line: float) -> float:
        return sum(matrix[h][a] for h in range(max_goals + 1) for a in range(max_goals + 1) if h + a > line)

    def side_over(dist: list[float], line: float) -> float:
        return sum(p for k, p in enumerate(dist) if k > line)

    outcomes: list[Outcome] = []

    # -- Draw No Bet (category 1) ---------------------------------------
    home_win = sum(matrix[h][a] for h in range(max_goals + 1) for a in range(max_goals + 1) if h > a)
    away_win = sum(matrix[h][a] for h in range(max_goals + 1) for a in range(max_goals + 1) if h < a)
    no_draw = home_win + away_win
    if no_draw > 0:
        outcomes += [
            Outcome("Draw No Bet", "Home", home_win / no_draw, "draw_no_bet", 5,
                    "Home team wins, with the stake refunded if the match is drawn."),
            Outcome("Draw No Bet", "Away", away_win / no_draw, "draw_no_bet", 5,
                    "Away team wins, with the stake refunded if the match is drawn."),
        ]

    # -- Total Goals, lines beyond what's already persisted (category 4) -
    for line in _EXTRA_TOTAL_LINES:
        over_p = total_over(float(line))
        outcomes += [
            Outcome(f"Total Goals {line}", f"Over {line}", over_p, f"ou_{line}", 5, f"Total match goals are greater than {line}."),
            Outcome(f"Total Goals {line}", f"Under {line}", 1 - over_p, f"ou_{line}", 5, f"Total match goals are less than {line}."),
        ]

    # -- Team Goals, home and away (categories 7, 8) ---------------------
    for line in _TEAM_GOAL_LINES:
        home_over = side_over(home_goals, float(line))
        away_over = side_over(away_goals, float(line))
        outcomes += [
            Outcome(f"Home Goals {line}", f"Over {line}", home_over, f"home_goals_{line}", 5, f"The home team scores more than {line} goals."),
            Outcome(f"Home Goals {line}", f"Under {line}", 1 - home_over, f"home_goals_{line}", 5, f"The home team scores fewer than {line} goals."),
            Outcome(f"Away Goals {line}", f"Over {line}", away_over, f"away_goals_{line}", 5, f"The away team scores more than {line} goals."),
            Outcome(f"Away Goals {line}", f"Under {line}", 1 - away_over, f"away_goals_{line}", 5, f"The away team scores fewer than {line} goals."),
        ]

    # -- Winning Margin (category 12) ------------------------------------
    margin_home = {1: 0.0, 2: 0.0, 3: 0.0, "4+": 0.0}
    margin_away = {1: 0.0, 2: 0.0, 3: 0.0, "4+": 0.0}
    draw_p = 0.0
    for h in range(max_goals + 1):
        for a in range(max_goals + 1):
            p = matrix[h][a]
            diff = h - a
            if diff == 0:
                draw_p += p
            elif diff > 0:
                margin_home["4+" if diff >= 4 else diff] += p
            else:
                margin_away["4+" if -diff >= 4 else -diff] += p
    outcomes += [
        Outcome("Winning Margin", f"Home by {'exactly ' if k != '4+' else ''}{k}", v, "winning_margin", 5,
                f"Home team wins by {'exactly ' + str(k) if k != '4+' else '4 or more'} goal(s).")
        for k, v in margin_home.items()
    ] + [
        Outcome("Winning Margin", f"Away by {'exactly ' if k != '4+' else ''}{k}", v, "winning_margin", 5,
                f"Away team wins by {'exactly ' + str(k) if k != '4+' else '4 or more'} goal(s).")
        for k, v in margin_away.items()
    ] + [
        Outcome("Winning Margin", "Draw", draw_p, "winning_margin", 5, "Match ends level."),
    ]

    # -- Clean sheets (category 13) --------------------------------------
    home_cs = away_goals[0]
    away_cs = home_goals[0]
    both_cs = matrix[0][0]
    outcomes += [
        Outcome("Home Clean Sheet", "Yes", home_cs, "home_clean_sheet", 5, "The away team fails to score."),
        Outcome("Home Clean Sheet", "No", 1 - home_cs, "home_clean_sheet", 5, "The away team scores at least once."),
        Outcome("Away Clean Sheet", "Yes", away_cs, "away_clean_sheet", 5, "The home team fails to score."),
        Outcome("Away Clean Sheet", "No", 1 - away_cs, "away_clean_sheet", 5, "The home team scores at least once."),
        Outcome("Both Teams Clean Sheet", "Yes", both_cs, "both_clean_sheet", 5, "The match finishes 0-0."),
        Outcome("Both Teams Clean Sheet", "No", 1 - both_cs, "both_clean_sheet", 5, "At least one team scores."),
    ]

    # -- Odd / Even goals (category 18) ----------------------------------
    def parity(dist_pairs) -> tuple[float, float]:
        even = sum(p for n, p in dist_pairs if n % 2 == 0)
        return even, 1 - even

    total_even, total_odd = parity(
        (h + a, matrix[h][a]) for h in range(max_goals + 1) for a in range(max_goals + 1)
    )
    home_even, home_odd = parity(enumerate(home_goals))
    away_even, away_odd = parity(enumerate(away_goals))
    outcomes += [
        Outcome("Total Goals Odd/Even", "Even", total_even, "total_goals_parity", 5, "Total match goals are an even number (0 counts as even)."),
        Outcome("Total Goals Odd/Even", "Odd", total_odd, "total_goals_parity", 5, "Total match goals are an odd number."),
        Outcome("Home Goals Odd/Even", "Even", home_even, "home_goals_parity", 5, "The home team scores an even number of goals."),
        Outcome("Home Goals Odd/Even", "Odd", home_odd, "home_goals_parity", 5, "The home team scores an odd number of goals."),
        Outcome("Away Goals Odd/Even", "Even", away_even, "away_goals_parity", 5, "The away team scores an even number of goals."),
        Outcome("Away Goals Odd/Even", "Odd", away_odd, "away_goals_parity", 5, "The away team scores an odd number of goals."),
    ]

    # -- Total Goals Range (category 24) ---------------------------------
    range_probs = {str(n): 0.0 for n in range(5)}
    range_probs["5+"] = 0.0
    for h in range(max_goals + 1):
        for a in range(max_goals + 1):
            total = h + a
            key = str(total) if total < 5 else "5+"
            range_probs[key] += matrix[h][a]
    outcomes += [
        Outcome("Total Goals Range", ("5+ goals" if k == "5+" else f"{k} goal" + ("" if k == "1" else "s")), v,
                "goals_range", 5, f"Total match goals is {'5 or more' if k == '5+' else k}.")
        for k, v in range_probs.items()
    ]

    # -- Result & Total Goals, Result & BTTS (categories 20, 21) ---------
    def result_of(h: int, a: int) -> str:
        return "Home" if h > a else ("Away" if h < a else "Draw")

    for line in ("1.5", "2.5"):
        line_f = float(line)
        buckets = {("Home", True): 0.0, ("Home", False): 0.0, ("Draw", True): 0.0,
                   ("Draw", False): 0.0, ("Away", True): 0.0, ("Away", False): 0.0}
        for h in range(max_goals + 1):
            for a in range(max_goals + 1):
                buckets[(result_of(h, a), h + a > line_f)] += matrix[h][a]
        outcomes += [
            Outcome(f"Result & Total Goals {line}", f"{result} & {'Over' if over else 'Under'} {line}", p,
                    f"result_ou_{line}", 5, f"{result} win combined with total goals {'over' if over else 'under'} {line}.")
            for (result, over), p in buckets.items()
        ]

        btts_buckets = {(True, True): 0.0, (True, False): 0.0, (False, True): 0.0, (False, False): 0.0}
        for h in range(max_goals + 1):
            for a in range(max_goals + 1):
                btts_buckets[(h >= 1 and a >= 1, h + a > line_f)] += matrix[h][a]
        outcomes += [
            Outcome(f"BTTS & Total Goals {line}", f"{'Yes' if btts else 'No'} & {'Over' if over else 'Under'} {line}", p,
                    f"btts_ou_{line}", 5, f"Both teams to score {'yes' if btts else 'no'} combined with total goals "
                    f"{'over' if over else 'under'} {line}.")
            for (btts, over), p in btts_buckets.items()
        ]

    result_btts_buckets = {("Home", True): 0.0, ("Home", False): 0.0, ("Draw", True): 0.0,
                            ("Draw", False): 0.0, ("Away", True): 0.0, ("Away", False): 0.0}
    for h in range(max_goals + 1):
        for a in range(max_goals + 1):
            result_btts_buckets[(result_of(h, a), h >= 1 and a >= 1)] += matrix[h][a]
    outcomes += [
        Outcome("Result & BTTS", f"{result} & BTTS {'Yes' if btts else 'No'}", p, "result_btts", 5,
                f"{result} win combined with both teams to score {'yes' if btts else 'no'}.")
        for (result, btts), p in result_btts_buckets.items()
    ]

    # -- Result & Clean Sheet (category 23) -------------------------------
    result_cs = {("Home", True): 0.0, ("Home", False): 0.0, ("Draw", True): 0.0,
                 ("Draw", False): 0.0, ("Away", True): 0.0, ("Away", False): 0.0}
    for h in range(max_goals + 1):
        for a in range(max_goals + 1):
            r = result_of(h, a)
            clean = (a == 0) if r == "Home" else ((h == 0) if r == "Away" else (h == 0 and a == 0))
            result_cs[(r, clean)] += matrix[h][a]
    outcomes += [
        Outcome("Result & Clean Sheet", f"{result} & Clean Sheet {'Yes' if cs else 'No'}", p, "result_clean_sheet", 5,
                f"{result} win combined with a clean sheet for the winning side"
                + (" (or a 0-0 draw)" if result == "Draw" else "") + (" -- yes" if cs else " -- no") + ".")
        for (result, cs), p in result_cs.items()
    ]

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

    ``model_breakdown["poisson"]`` carries that component's own
    lambda_home/lambda_away, persisted for every prediction already -- it was
    never added just for this. Rebuilding the same Dixon-Coles matrix from
    them is what lets the browse page offer a much larger set of markets
    (winning margin, clean sheets, goal parity, and more) without a schema
    change or a single new number computed at generation time.
    """

    matches_available = round((prediction.data_quality_score or 0.0) * 10)
    outcomes = build_outcome_registry(
        prediction.home_win,
        prediction.draw,
        prediction.away_win,
        prediction.over_probabilities or {},
        prediction.btts_yes,
        prediction.btts_no,
        prediction.correct_score_probabilities or {},
        matches_available=matches_available,
    )

    poisson = (prediction.model_breakdown or {}).get("poisson") or {}
    lambda_home, lambda_away = poisson.get("lambda_home"), poisson.get("lambda_away")
    if lambda_home is not None and lambda_away is not None:
        outcomes += matrix_derived_outcomes(lambda_home, lambda_away, matches_available)

    return outcomes
