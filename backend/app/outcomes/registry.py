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
from types import SimpleNamespace

from app.prediction_models.corners_model import build_corners_matrix, markets_from_corners_matrix
from app.prediction_models.poisson_model import HT_MAX_GOALS, build_score_matrix, markets_from_matrix


@dataclass(frozen=True)
class Outcome:
    market: str
    selection: str
    probability: float
    mutually_exclusive_group: str
    min_data_requirement: int
    definition: str


# Groups whose members are unions built from another group's own outcomes --
# e.g. Double Chance's Home/Draw = P(Home) + P(Draw) -- so a member is
# always at least as likely as what it's built from. Letting one of these
# compete for the headline "most likely outcome" or a "what else does the
# model like" slot would just restate a real pick more loosely, never add
# one; see app.outcomes.engine.secondary_outcomes.
DOMINANT_UNION_GROUPS = frozenset({
    "double_chance", "ht_double_chance", "ht_ft_double_chance", "ht_double_chance_btts",
    "dc_btts", "dc_total_goals_1.5", "dc_total_goals_2.5", "dc_total_goals_3.5", "dc_total_goals_4.5",
})

# Groups whose displayed selections do not actually partition 100% of
# probability between them -- either because they're unions of each other
# (DOMINANT_UNION_GROUPS) or because only a truncated top-N of many possible
# scorelines is kept (Correct Score, HT Correct Score). The Markets page's
# "exactly one of these happens, adding up to 100%" framing is only true
# outside this set -- see routes_predictions.py's `outcomes` endpoint.
NOT_A_FULL_PARTITION_GROUPS = DOMINANT_UNION_GROUPS | frozenset({"correct_score", "ht_correct_score"})


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
# Half-goal lines only (see matrix_derived_outcomes' own Asian Handicap
# section for why: a half-integer margin can never land exactly on 0, so
# there's no push/void case a whole-integer line would need). Capped at
# 3.5 -- the deepest real line genuinely observed on SportyBet's own
# market 16 in either direction (Real Madrid vs Sevilla/Bayern Munich vs
# RB Leipzig for -3.5; Betis vs Barcelona/Freiburg vs Bayern Munich for
# +3.5); nothing seen quoting 4.5.
_ASIAN_HANDICAP_LINES = (0.5, 1.5, 2.5, 3.5)

_RESULT_SHORT = {"Home": "1", "Draw": "X", "Away": "2"}
_RESULT_PHRASE = {
    "Home": "the home team is ahead",
    "Draw": "the scores are level",
    "Away": "the away team is ahead",
}
_DOUBLE_CHANCE_MEMBERS = {
    "Home": ("Home/Draw", "Home/Away"),
    "Draw": ("Home/Draw", "Draw/Away"),
    "Away": ("Home/Away", "Draw/Away"),
}
_DOUBLE_CHANCE_PHRASE = {
    "Home/Draw": "the home team is not losing",
    "Home/Away": "the match is not drawn",
    "Draw/Away": "the away team is not losing",
}
# The inverse of _DOUBLE_CHANCE_MEMBERS -- which two results make up each
# double chance selection -- for combos (Double Chance & BTTS/Total Goals)
# that need to sum a double chance name's own members rather than look up
# which double chance names a single result belongs to.
_DOUBLE_CHANCE_RESULTS = {
    "Home/Draw": ("Home", "Draw"),
    "Home/Away": ("Home", "Away"),
    "Draw/Away": ("Draw", "Away"),
}


def matrix_derived_outcomes(lambda_home: float, lambda_away: float, matches_available: int) -> list[Outcome]:
    """Every full-time outcome that is pure arithmetic over the model's own
    scoreline matrix -- no new statistics collected, no new model fit, just a
    different slice of a distribution the Poisson component already computes
    for Correct Score. Half-time markets are the same idea applied to a
    half-time-scaled matrix -- see ht_matrix_derived_outcomes below.

    Deliberately does not attempt cards/fouls (no predictive model exists for
    them, though the raw historical counts are stored), player markets (no
    player-level data source), or goal-timing / first-to-score markets (no
    minute-level event model). Inventing numbers for those would look like
    analysis and be a guess. Corners get the identical treatment but from
    their own matrix -- see ``corners_outcomes`` below.
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

    # -- Home/Away No Bet (category 1a) ----------------------------------
    # The same "void on an unwanted result" shape as Draw No Bet, just
    # voiding a different one: Home No Bet (SportyBet market 12) refunds
    # the stake if the home side wins; Away No Bet (market 13) refunds it
    # if the away side wins. The voided result isn't one of the two
    # selections here, same as a draw never being one of Draw No Bet's.
    draw_prob = 1.0 - home_win - away_win
    not_home = draw_prob + away_win
    if not_home > 0:
        outcomes += [
            Outcome("Home No Bet", "Draw", draw_prob / not_home, "home_no_bet", 5,
                    "Match is drawn, with the stake refunded if the home team wins."),
            Outcome("Home No Bet", "Away", away_win / not_home, "home_no_bet", 5,
                    "Away team wins, with the stake refunded if the home team wins."),
        ]
    not_away = home_win + draw_prob
    if not_away > 0:
        outcomes += [
            Outcome("Away No Bet", "Home", home_win / not_away, "away_no_bet", 5,
                    "Home team wins, with the stake refunded if the away team wins."),
            Outcome("Away No Bet", "Draw", draw_prob / not_away, "away_no_bet", 5,
                    "Match is drawn, with the stake refunded if the away team wins."),
        ]

    # -- Handicap, 3-way goal-line (category 1b) -------------------------
    # The bookmaker "Handicap" market, distinct from a 2-way Asian Handicap:
    # a line applies directly to the home team's goal tally before comparing
    # (Handicap -1 means the home side must win by 2+ to cover; Handicap +1
    # means a 1-goal home loss still covers), and the three selections
    # always partition 100% -- no push/void accounting, which an Asian
    # Handicap's quarter lines would need and this registry has nowhere else
    # to represent (see this module's docstring on mutually_exclusive_group).
    # Only full-time is offered -- unlike Draw No Bet and Winning Margin
    # above, bookmakers essentially never quote a half-time handicap, so
    # there's no real-world line to mirror into ht_matrix_derived_outcomes.
    for line in (-5, -4, -3, -2, -1, 1, 2, 3, 4, 5):
        bucket = {"Home": 0.0, "Draw": 0.0, "Away": 0.0}
        for h in range(max_goals + 1):
            for a in range(max_goals + 1):
                margin = (h + line) - a
                key = "Home" if margin > 0 else ("Away" if margin < 0 else "Draw")
                bucket[key] += matrix[h][a]

        n = abs(line)
        sign = f"{line:+d}"
        if line < 0:
            home_def = f"Home team wins by {n + 1} or more goals (covers a {sign} handicap)."
            draw_def = f"Home team wins by exactly {n} goal(s) (the handicap line is exactly covered)."
            away_def = f"Home team wins by fewer than {n} goal(s), draws, or loses outright (handicap not covered)."
        else:
            home_def = f"Home team wins, draws, or loses by fewer than {n} goal(s) (covers a {sign} handicap)."
            draw_def = f"Home team loses by exactly {n} goal(s) (the handicap line is exactly covered)."
            away_def = f"Home team loses by more than {n} goal(s) (handicap not covered)."

        outcomes += [
            Outcome(f"Handicap {sign}", "Home", bucket["Home"], f"handicap_{line}", 5, home_def),
            Outcome(f"Handicap {sign}", "Draw", bucket["Draw"], f"handicap_{line}", 5, draw_def),
            Outcome(f"Handicap {sign}", "Away", bucket["Away"], f"handicap_{line}", 5, away_def),
        ]

    # -- Asian Handicap, 2-way half-line (category 1c) -------------------
    # SportyBet's own "Handicap" market id 16 is distinct from id 14's
    # 3-way integer-line version above: id 16 also quotes whole-integer
    # lines (hcp=0, hcp=1, ...), but those can push (an exact-margin draw
    # voids the bet), the same accounting problem that kept Asian Handicap
    # out of this registry entirely before -- see the 3-way block's own
    # comment. A HALF-integer line never can: (h + line) - a is never
    # exactly 0 when line ends in .5, so it's always a clean 2-way split
    # with no push case to represent. Only those lines are built here.
    for line in _ASIAN_HANDICAP_LINES:
        for signed in (-line, line):
            home_covers = sum(
                matrix[h][a]
                for h in range(max_goals + 1) for a in range(max_goals + 1)
                if (h + signed) - a > 0
            )
            sign = f"{signed:+.1f}"
            if signed < 0:
                home_def = f"Home team wins by {abs(signed):.1f} or more goals (covers a {sign} handicap)."
                away_def = f"Home team wins by less than {abs(signed):.1f} goals, draws, or loses outright (handicap not covered)."
            else:
                home_def = f"Home team wins, draws, or loses by less than {signed:.1f} goals (covers a {sign} handicap)."
                away_def = f"Home team loses by {signed:.1f} or more goals (handicap not covered)."
            outcomes += [
                Outcome(f"Asian Handicap {sign}", "Home", home_covers, f"asian_handicap_{signed}", 5, home_def),
                Outcome(f"Asian Handicap {sign}", "Away", 1 - home_covers, f"asian_handicap_{signed}", 5, away_def),
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

    # -- Which Team To Score (category 13b) ------------------------------
    # SportyBet's own 4-way split (market 30) of the same scoring event the
    # clean-sheet markets above already describe piecemeal -- exactly one
    # of "neither scores", "only the home side scores", "only the away
    # side scores", or "both score" is true for any final score.
    wts = {"None": 0.0, "Only Home": 0.0, "Only Away": 0.0, "Both": 0.0}
    for h in range(max_goals + 1):
        for a in range(max_goals + 1):
            p = matrix[h][a]
            if h == 0 and a == 0:
                wts["None"] += p
            elif a == 0:
                wts["Only Home"] += p
            elif h == 0:
                wts["Only Away"] += p
            else:
                wts["Both"] += p
    _wts_defs = {
        "None": "Neither team scores.",
        "Only Home": "The home team scores and the away team does not.",
        "Only Away": "The away team scores and the home team does not.",
        "Both": "Both teams score.",
    }
    outcomes += [Outcome("Which Team To Score", k, v, "which_team_to_score", 5, _wts_defs[k]) for k, v in wts.items()]

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

    # -- Goal Range, coarse bands (category 24b) -------------------------
    # SportyBet's own "Goal Range" (market 25, sr:goal_range:7+) -- coarser,
    # non-overlapping bands than Total Goals Range above, which already
    # mirrors its own exact-goals market (id 21). A different real market,
    # not a re-bucketing of the same one, so it gets its own name here too.
    _coarse_bands = (("0-1", 0, 1), ("2-3", 2, 3), ("4-6", 4, 6))
    coarse_probs: dict[str, float] = {}
    for label, lo, hi in _coarse_bands:
        coarse_probs[label] = sum(
            matrix[h][a] for h in range(max_goals + 1) for a in range(max_goals + 1) if lo <= h + a <= hi
        )
    coarse_probs["7+"] = max(0.0, 1.0 - sum(coarse_probs.values()))
    outcomes += [
        Outcome("Goal Range", k, v, "goal_range_coarse", 5,
                "Total match goals are 7 or more." if k == "7+" else f"Total match goals are {k.replace('-', ' to ')}.")
        for k, v in coarse_probs.items()
    ]

    # -- Result & Total Goals, Result & BTTS (categories 20, 21) ---------
    def result_of(h: int, a: int) -> str:
        return "Home" if h > a else ("Away" if h < a else "Draw")

    for line in ("1.5", "2.5", "3.5", "4.5"):
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

        # Double Chance & Total Goals (SportyBet market 547) -- a union of
        # this same line's Result & Total Goals buckets, the same
        # "dominant union" shape plain Double Chance already is above.
        outcomes += [
            Outcome(f"Double Chance & Total Goals {line}", f"{dc} & {'Over' if over else 'Under'} {line}",
                    sum(buckets[(r, over)] for r in members), f"dc_total_goals_{line}", 5,
                    f"{_DOUBLE_CHANCE_PHRASE[dc].capitalize()}, combined with total goals "
                    f"{'over' if over else 'under'} {line}.")
            for dc, members in _DOUBLE_CHANCE_RESULTS.items() for over in (True, False)
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

    # Double Chance & BTTS (SportyBet market 546) -- a union of Result &
    # BTTS's own buckets, same dominant-union shape as above.
    outcomes += [
        Outcome("Double Chance & BTTS", f"{dc} & BTTS {'Yes' if btts else 'No'}",
                sum(result_btts_buckets[(r, btts)] for r in members), "dc_btts", 5,
                f"{_DOUBLE_CHANCE_PHRASE[dc].capitalize()}, combined with both teams to score {'yes' if btts else 'no'}.")
        for dc, members in _DOUBLE_CHANCE_RESULTS.items() for btts in (True, False)
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


def corners_outcomes(lambda_home_corners: float, lambda_away_corners: float, matches_available: int) -> list[Outcome]:
    """Total Corners O/U and Most Corners -- the corners analogues of Total
    Goals and Match Result, built from ``corners_model``'s own independent-
    Poisson matrix rather than the goals one. Only ever called with real
    lambdas fit from a league's own backfilled corners history (see
    ``corners_model.expected_corners``); a league without that history never
    reaches here at all -- see ``outcomes_from_prediction``.
    """

    matrix = build_corners_matrix(lambda_home_corners, lambda_away_corners)
    markets = markets_from_corners_matrix(matrix, lambda_home_corners, lambda_away_corners)

    outcomes: list[Outcome] = [
        Outcome("Most Corners", "Home", markets.home_most_corners, "most_corners", 5,
                "The home team wins more corners than the away team."),
        Outcome("Most Corners", "Away", markets.away_most_corners, "most_corners", 5,
                "The away team wins more corners than the home team."),
        Outcome("Most Corners", "Equal", markets.equal_corners, "most_corners", 5,
                "Both teams win the same number of corners."),
    ]

    for line, over_p in markets.over_probabilities.items():
        outcomes += [
            Outcome(f"Total Corners {line}", f"Over {line}", over_p, f"corners_ou_{line}", 5,
                    f"Total match corners are greater than {line}."),
            Outcome(f"Total Corners {line}", f"Under {line}", 1 - over_p, f"corners_ou_{line}", 5,
                    f"Total match corners are less than {line}."),
        ]

    return [o for o in outcomes if matches_available >= o.min_data_requirement]


def ht_matrix_derived_outcomes(lambda_home_ht: float, lambda_away_ht: float, matches_available: int) -> list[Outcome]:
    """Half-time markets: the identical Dixon-Coles machinery as
    matrix_derived_outcomes above, just built from half-time-scaled lambdas
    (app.prediction_models.poisson_model.ht_goal_fraction) and a smaller
    max_goals, since more than a handful of goals inside 45 minutes is
    vanishingly rare.

    Reuses markets_from_matrix wholesale for HT Result, the HT goal lines,
    HT BTTS and HT Correct Score -- it already computes exactly those from
    any two lambdas, home_win/draw/away_win just mean "ahead at the break"
    instead of "ahead at full time" here. Only HT Double Chance, HT Exact
    Goals, HT Odd/Even and HT Multigoals need the raw matrix directly, the
    same way FT's own odd/even and goals-range markets do above.

    HT Multigoals bands are non-overlapping (0-1 / 2-3 / 4+) rather than the
    overlapping bands a bookmaker's own "Multigoals" market advertises (1-2,
    1-3, 2-3, ...) -- every market in this registry is required to belong to
    a mutually_exclusive_group that sums to 1 (see this module's docstring),
    and overlapping bands can't satisfy that.
    """

    matrix = build_score_matrix(lambda_home_ht, lambda_away_ht, max_goals=HT_MAX_GOALS)
    max_goals = len(matrix) - 1
    ht = markets_from_matrix(matrix, lambda_home_ht, lambda_away_ht)

    outcomes: list[Outcome] = [
        Outcome("HT Result", "Home", ht.home_win, "ht_1x2", 5, "Home team is ahead at half-time."),
        Outcome("HT Result", "Draw", ht.draw, "ht_1x2", 5, "Scores are level at half-time."),
        Outcome("HT Result", "Away", ht.away_win, "ht_1x2", 5, "Away team is ahead at half-time."),
        Outcome("HT Double Chance", "Home/Draw", ht.home_win + ht.draw, "ht_double_chance", 5,
                "Home team is ahead or level at half-time."),
        Outcome("HT Double Chance", "Home/Away", ht.home_win + ht.away_win, "ht_double_chance", 5,
                "Either team is ahead at half-time (not level)."),
        Outcome("HT Double Chance", "Draw/Away", ht.draw + ht.away_win, "ht_double_chance", 5,
                "Scores are level or the away team is ahead at half-time."),
        Outcome("HT Both Teams To Score", "Yes", ht.btts_yes, "ht_btts", 5, "Both teams have scored by half-time."),
        Outcome("HT Both Teams To Score", "No", ht.btts_no, "ht_btts", 5, "At least one team has not scored by half-time."),
    ]

    for line, over_p in ht.over_probabilities.items():
        outcomes += [
            Outcome(f"HT Total Goals {line}", f"Over {line}", over_p, f"ht_ou_{line}", 5,
                    f"Total goals at half-time are greater than {line}."),
            Outcome(f"HT Total Goals {line}", f"Under {line}", 1 - over_p, f"ht_ou_{line}", 5,
                    f"Total goals at half-time are less than {line}."),
        ]

    for score, prob in ht.correct_score_probabilities.items():
        outcomes.append(
            Outcome("HT Correct Score", score, prob, "ht_correct_score", 10, f"Score at half-time is exactly {score}.")
        )

    def parity(pairs) -> tuple[float, float]:
        even = sum(p for n, p in pairs if n % 2 == 0)
        return even, 1 - even

    total_even, total_odd = parity(
        (h + a, matrix[h][a]) for h in range(max_goals + 1) for a in range(max_goals + 1)
    )
    outcomes += [
        Outcome("HT Total Goals Odd/Even", "Even", total_even, "ht_goals_parity", 5,
                "Total goals at half-time are an even number (0 counts as even)."),
        Outcome("HT Total Goals Odd/Even", "Odd", total_odd, "ht_goals_parity", 5,
                "Total goals at half-time are an odd number."),
    ]

    exact = {"0": 0.0, "1": 0.0, "2": 0.0, "3+": 0.0}
    for h in range(max_goals + 1):
        for a in range(max_goals + 1):
            total = h + a
            key = str(total) if total < 3 else "3+"
            exact[key] += matrix[h][a]
    outcomes += [
        Outcome("HT Exact Goals", k, v, "ht_exact_goals", 5,
                f"Total goals at half-time is {'3 or more' if k == '3+' else k}.")
        for k, v in exact.items()
    ]

    multi = {"0-1": 0.0, "2-3": 0.0, "4+": 0.0}
    for h in range(max_goals + 1):
        for a in range(max_goals + 1):
            total = h + a
            key = "0-1" if total <= 1 else ("2-3" if total <= 3 else "4+")
            multi[key] += matrix[h][a]
    outcomes += [
        Outcome("HT Multigoals", k, v, "ht_multigoals", 5, f"Total goals at half-time fall within the {k} range.")
        for k, v in multi.items()
    ]

    return [o for o in outcomes if matches_available >= o.min_data_requirement]


_SECOND_HALF_PHRASE = {
    "Home": "the home team scores more goals than the away team",
    "Draw": "both teams score the same number of goals",
    "Away": "the away team scores more goals than the home team",
}

# Single, standard lines for the combos below -- multiplying every combo by
# every goal line this project already tracks would bury a handful of useful
# markets in dozens of near-duplicates. Each is the one a bookmaker would
# actually quote for this exact combo.
_HT_RESULT_GOALS_LINES = ("1.5", "2.5")  # mirrors matrix_derived_outcomes' own Result & Total Goals lines
_HT_FT_GOALS_LINE = "2.5"
_HT_VS_2H_GOALS_LINE = "1.5"


def ht_ft_joint_outcomes(
    lambda_home: float,
    lambda_away: float,
    lambda_home_ht: float,
    lambda_away_ht: float,
    matches_available: int,
) -> list[Outcome]:
    """Every market that needs BOTH halves at once: the classic HT/FT grid,
    every combo built on top of it, half-time-result-plus-full-time-stat
    combos, first-half-vs-second-half markets, and "which half had more
    goals".

    Built from exactly the two pieces every other half-time market here
    already uses -- nothing new is estimated. The second half's own lambda
    is simply the remainder, ``lambda - lambda_ht``: goals in two
    non-overlapping stretches of an independent Poisson process add up the
    same way the whole match's scoring rate does, which is the identical
    arithmetic that produced ``lambda_home_ht`` in the first place (see
    ``ensemble.py``'s use of ``ht_goal_fraction``). No new statistic is fit
    and no minute-level event data is required -- see this module's
    docstring, and ``matrix_derived_outcomes``'s, for why that line isn't
    crossed elsewhere either.

    The full joint (HT scoreline, 2H scoreline) distribution is walked once,
    in a single pass, and every combo below reads off that same walk rather
    than five separate approximations of it.

    Two of the resulting groups -- HT/FT combined with Double Chance, either
    half's -- are unions of each other the same way plain Double Chance is
    (see ``DOMINANT_UNION_GROUPS``): each event satisfies more than one of a
    group's own selections, so those groups don't sum to 1 and are excluded
    from headline/secondary-outcome ranking the same way.

    Deliberately not included: "HT + FT Result" (identical to HT/FT itself --
    same joint distribution, same numbers, so it isn't built a second time
    under a second name).
    """

    ht_matrix = build_score_matrix(lambda_home_ht, lambda_away_ht, max_goals=HT_MAX_GOALS)
    # Floored, not left to go non-positive -- ht_goal_fraction is clamped to
    # [0.25, 0.65] so lambda_ht should never reach lambda itself, but a
    # second half needs some scoring rate to build a matrix from regardless.
    lambda_home_2h = max(lambda_home - lambda_home_ht, 0.05)
    lambda_away_2h = max(lambda_away - lambda_away_ht, 0.05)
    h2_matrix = build_score_matrix(lambda_home_2h, lambda_away_2h, max_goals=HT_MAX_GOALS)
    ht_max = len(ht_matrix) - 1
    h2_max = len(h2_matrix) - 1

    def result_of(h: int, a: int) -> str:
        return "Home" if h > a else ("Away" if h < a else "Draw")

    def goals_bucket(total: int) -> str:
        return str(total) if total < 5 else "5+"

    ht_ft_p: dict[tuple[str, str], float] = {}
    ht_ft_dc_p: dict[tuple[str, str], float] = {}
    ht_result_goals_p: dict[tuple[str, str, bool], float] = {}
    ht_result_btts_p: dict[tuple[str, bool], float] = {}
    ht_dc_btts_p: dict[tuple[str, bool], float] = {}
    ht_ft_goals_p: dict[tuple[str, str, bool], float] = {}
    ht_ft_btts_p: dict[tuple[str, str, bool], float] = {}
    ht_ft_exact_p: dict[tuple[str, str, str], float] = {}
    ht_2h_result_p: dict[tuple[str, str], float] = {}
    ht_2h_goals_p: dict[tuple[bool, bool], float] = {}
    half_most_goals_p = {"1st Half": 0.0, "2nd Half": 0.0, "Equal": 0.0}

    for h1 in range(ht_max + 1):
        for a1 in range(ht_max + 1):
            p_ht = ht_matrix[h1][a1]
            if p_ht <= 0:
                continue
            ht_result = result_of(h1, a1)
            ht_total = h1 + a1

            for h2 in range(h2_max + 1):
                for a2 in range(h2_max + 1):
                    p = p_ht * h2_matrix[h2][a2]
                    if p <= 0:
                        continue

                    h2_total = h2 + a2
                    ft_h, ft_a = h1 + h2, a1 + a2
                    ft_result = result_of(ft_h, ft_a)
                    ft_total = ft_h + ft_a
                    ft_btts = ft_h >= 1 and ft_a >= 1

                    key2 = (ht_result, ft_result)
                    ht_ft_p[key2] = ht_ft_p.get(key2, 0.0) + p

                    for ht_dc in _DOUBLE_CHANCE_MEMBERS[ht_result]:
                        for ft_dc in _DOUBLE_CHANCE_MEMBERS[ft_result]:
                            k = (ht_dc, ft_dc)
                            ht_ft_dc_p[k] = ht_ft_dc_p.get(k, 0.0) + p
                        k = (ht_dc, ft_btts)
                        ht_dc_btts_p[k] = ht_dc_btts_p.get(k, 0.0) + p

                    for line in _HT_RESULT_GOALS_LINES:
                        k = (ht_result, line, ft_total > float(line))
                        ht_result_goals_p[k] = ht_result_goals_p.get(k, 0.0) + p

                    k = (ht_result, ft_btts)
                    ht_result_btts_p[k] = ht_result_btts_p.get(k, 0.0) + p

                    k = (ht_result, ft_result, ft_total > float(_HT_FT_GOALS_LINE))
                    ht_ft_goals_p[k] = ht_ft_goals_p.get(k, 0.0) + p

                    k = (ht_result, ft_result, ft_btts)
                    ht_ft_btts_p[k] = ht_ft_btts_p.get(k, 0.0) + p

                    k = (ht_result, ft_result, goals_bucket(ft_total))
                    ht_ft_exact_p[k] = ht_ft_exact_p.get(k, 0.0) + p

                    k = (ht_result, result_of(h2, a2))
                    ht_2h_result_p[k] = ht_2h_result_p.get(k, 0.0) + p

                    k = (ht_total > float(_HT_VS_2H_GOALS_LINE), h2_total > float(_HT_VS_2H_GOALS_LINE))
                    ht_2h_goals_p[k] = ht_2h_goals_p.get(k, 0.0) + p

                    if ht_total > h2_total:
                        half_most_goals_p["1st Half"] += p
                    elif ht_total < h2_total:
                        half_most_goals_p["2nd Half"] += p
                    else:
                        half_most_goals_p["Equal"] += p

    outcomes: list[Outcome] = []

    outcomes += [
        Outcome(
            "HT/FT", f"{_RESULT_SHORT[h]}/{_RESULT_SHORT[f]}", p, "ht_ft", 5,
            f"At half-time {_RESULT_PHRASE[h]}; at full-time {_RESULT_PHRASE[f]}.",
        )
        for (h, f), p in ht_ft_p.items()
    ]

    outcomes += [
        Outcome(
            "HT + FT Double Chance", f"{ht_dc} & {ft_dc}", p, "ht_ft_double_chance", 5,
            f"At half-time {_DOUBLE_CHANCE_PHRASE[ht_dc]}; at full-time {_DOUBLE_CHANCE_PHRASE[ft_dc]}.",
        )
        for (ht_dc, ft_dc), p in ht_ft_dc_p.items()
    ]

    outcomes += [
        Outcome(
            f"HT Result & Total Goals {line}", f"{h} & {'Over' if over else 'Under'} {line}", p,
            f"ht_result_goals_{line}", 5,
            f"At half-time {_RESULT_PHRASE[h]}, combined with full-time total goals "
            f"{'over' if over else 'under'} {line}.",
        )
        for (h, line, over), p in ht_result_goals_p.items()
    ]

    outcomes += [
        Outcome(
            "HT Result & BTTS", f"{h} & BTTS {'Yes' if btts else 'No'}", p, "ht_result_btts", 5,
            f"At half-time {_RESULT_PHRASE[h]}, combined with both teams to score "
            f"{'yes' if btts else 'no'} by full-time.",
        )
        for (h, btts), p in ht_result_btts_p.items()
    ]

    outcomes += [
        Outcome(
            "HT Double Chance & BTTS", f"{ht_dc} & BTTS {'Yes' if btts else 'No'}", p, "ht_double_chance_btts", 5,
            f"At half-time {_DOUBLE_CHANCE_PHRASE[ht_dc]}, combined with both teams to score "
            f"{'yes' if btts else 'no'} by full-time.",
        )
        for (ht_dc, btts), p in ht_dc_btts_p.items()
    ]

    outcomes += [
        Outcome(
            f"HT/FT & Total Goals {_HT_FT_GOALS_LINE}",
            f"{_RESULT_SHORT[h]}/{_RESULT_SHORT[f]} & {'Over' if over else 'Under'} {_HT_FT_GOALS_LINE}", p,
            f"ht_ft_goals_{_HT_FT_GOALS_LINE}", 10,
            f"At half-time {_RESULT_PHRASE[h]} and at full-time {_RESULT_PHRASE[f]}, combined with full-time "
            f"total goals {'over' if over else 'under'} {_HT_FT_GOALS_LINE}.",
        )
        for (h, f, over), p in ht_ft_goals_p.items()
    ]

    outcomes += [
        Outcome(
            "HT/FT & BTTS", f"{_RESULT_SHORT[h]}/{_RESULT_SHORT[f]} & BTTS {'Yes' if btts else 'No'}", p,
            "ht_ft_btts", 10,
            f"At half-time {_RESULT_PHRASE[h]} and at full-time {_RESULT_PHRASE[f]}, combined with both teams "
            f"to score {'yes' if btts else 'no'} by full-time.",
        )
        for (h, f, btts), p in ht_ft_btts_p.items()
    ]

    outcomes += [
        Outcome(
            "HT/FT & Exact Goals",
            f"{_RESULT_SHORT[h]}/{_RESULT_SHORT[f]} & " + ("5+ goals" if bucket == "5+" else f"{bucket} goal" + ("" if bucket == "1" else "s")),
            p, "ht_ft_exact_goals", 10,
            f"At half-time {_RESULT_PHRASE[h]} and at full-time {_RESULT_PHRASE[f]}, with total match goals "
            f"{'5 or more' if bucket == '5+' else bucket}.",
        )
        for (h, f, bucket), p in ht_ft_exact_p.items()
    ]

    outcomes += [
        Outcome(
            "HT + 2H Result", f"{h} & {s}", p, "ht_2h_result", 5,
            f"At half-time {_RESULT_PHRASE[h]}; in the second half alone, {_SECOND_HALF_PHRASE[s]}.",
        )
        for (h, s), p in ht_2h_result_p.items()
    ]

    # Home/Away Team To Win Both Halves / Either Half (SportyBet markets
    # 48-51) -- the same ht_2h_result_p joint above, just re-sliced to one
    # team's own two outcomes instead of shown as the full 3x3 grid. "Both
    # halves" is one specific (ht, h2) pair; "either half" is every pair
    # where that team won at least one of the two.
    home_both = ht_2h_result_p.get(("Home", "Home"), 0.0)
    away_both = ht_2h_result_p.get(("Away", "Away"), 0.0)
    home_either = sum(p for (h, s), p in ht_2h_result_p.items() if h == "Home" or s == "Home")
    away_either = sum(p for (h, s), p in ht_2h_result_p.items() if h == "Away" or s == "Away")
    outcomes += [
        Outcome("Home Team To Win Both Halves", "Yes", home_both, "home_win_both_halves", 5,
                "The home team is ahead at half-time and wins the second half outright."),
        Outcome("Home Team To Win Both Halves", "No", 1 - home_both, "home_win_both_halves", 5,
                "The home team fails to lead at half-time, or fails to win the second half outright, or both."),
        Outcome("Away Team To Win Both Halves", "Yes", away_both, "away_win_both_halves", 5,
                "The away team is ahead at half-time and wins the second half outright."),
        Outcome("Away Team To Win Both Halves", "No", 1 - away_both, "away_win_both_halves", 5,
                "The away team fails to lead at half-time, or fails to win the second half outright, or both."),
        Outcome("Home Team To Win Either Half", "Yes", home_either, "home_win_either_half", 5,
                "The home team is ahead at half-time, wins the second half outright, or both."),
        Outcome("Home Team To Win Either Half", "No", 1 - home_either, "home_win_either_half", 5,
                "The home team is neither ahead at half-time nor wins the second half outright."),
        Outcome("Away Team To Win Either Half", "Yes", away_either, "away_win_either_half", 5,
                "The away team is ahead at half-time, wins the second half outright, or both."),
        Outcome("Away Team To Win Either Half", "No", 1 - away_either, "away_win_either_half", 5,
                "The away team is neither ahead at half-time nor wins the second half outright."),
    ]

    outcomes += [
        Outcome(
            f"HT & 2H Total Goals {_HT_VS_2H_GOALS_LINE}",
            f"{'Over' if ht_over else 'Under'} {_HT_VS_2H_GOALS_LINE} HT & {'Over' if h2_over else 'Under'} {_HT_VS_2H_GOALS_LINE} 2H",
            p, f"ht_2h_goals_{_HT_VS_2H_GOALS_LINE}", 5,
            f"Half-time goals {'over' if ht_over else 'under'} {_HT_VS_2H_GOALS_LINE}, combined with second-half-"
            f"only goals {'over' if h2_over else 'under'} {_HT_VS_2H_GOALS_LINE}.",
        )
        for (ht_over, h2_over), p in ht_2h_goals_p.items()
    ]

    outcomes += [
        Outcome(
            "Half With Most Goals", k, v, "half_most_goals", 5,
            "The first half has more total goals than the second."
            if k == "1st Half" else "The second half has more total goals than the first."
            if k == "2nd Half" else "Both halves have the same total goals.",
        )
        for k, v in half_most_goals_p.items()
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

    lambda_home_ht, lambda_away_ht = poisson.get("lambda_home_ht"), poisson.get("lambda_away_ht")
    if lambda_home_ht is not None and lambda_away_ht is not None:
        outcomes += ht_matrix_derived_outcomes(lambda_home_ht, lambda_away_ht, matches_available)

    if lambda_home is not None and lambda_away is not None and lambda_home_ht is not None and lambda_away_ht is not None:
        outcomes += ht_ft_joint_outcomes(lambda_home, lambda_away, lambda_home_ht, lambda_away_ht, matches_available)

    lambda_home_corners, lambda_away_corners = poisson.get("lambda_home_corners"), poisson.get("lambda_away_corners")
    if lambda_home_corners is not None and lambda_away_corners is not None:
        outcomes += corners_outcomes(lambda_home_corners, lambda_away_corners, matches_available)

    return outcomes


def full_registry_market_count() -> int:
    """How many distinct markets the registry scores for a match with
    complete data -- every lambda the Poisson model can produce (full time,
    half time, corners) present, and enough history for every data-gated
    market (Correct Score included). This is a capability figure ("what can
    this system score"), not a per-match guarantee -- a thin-data or
    corners-less match offers fewer.

    Computed from the same code path real predictions run through
    (``outcomes_from_prediction``) against representative, non-degenerate
    synthetic inputs, rather than hardcoded -- the landing page's own public
    stats endpoint quotes this number specifically so it can't go stale the
    way a typed-in literal did once the registry grew past it.
    """

    synthetic = SimpleNamespace(
        home_win=0.45, draw=0.28, away_win=0.27,
        over_probabilities={"0.5": 0.95, "1.5": 0.78, "2.5": 0.52, "3.5": 0.28, "4.5": 0.12},
        btts_yes=0.55, btts_no=0.45,
        correct_score_probabilities={
            "1-0": 0.11, "2-1": 0.10, "1-1": 0.09, "0-0": 0.08, "2-0": 0.07,
            "0-1": 0.06, "1-2": 0.05, "3-1": 0.04, "2-2": 0.04, "0-2": 0.03,
        },
        data_quality_score=1.0,
        model_breakdown={
            "poisson": {
                "lambda_home": 1.6, "lambda_away": 1.1,
                "lambda_home_ht": 0.7, "lambda_away_ht": 0.5,
                "lambda_home_corners": 6.0, "lambda_away_corners": 4.5,
            },
        },
    )
    return len({o.market for o in outcomes_from_prediction(synthetic)})


def find_outcome(prediction, market: str, selection: str) -> Outcome | None:
    """The one outcome, if any, matching this exact market/selection pair.

    Used two ways: to validate a curated "Guda Pick" against a real model
    output rather than trusting whatever an admin typed, and to recompute
    its current probability at read time rather than storing a frozen
    snapshot. Returns ``None`` for a pair that either never existed (a typo,
    or a market that needs more data than this match currently has) or no
    longer does (e.g. the match finished and its live markets changed) --
    the caller's job is to treat that as "nothing to show", not an error.
    """

    for outcome in outcomes_from_prediction(prediction):
        if outcome.market == market and outcome.selection == selection:
            return outcome
    return None
