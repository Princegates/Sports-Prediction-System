"""grade_outcome: a (market, selection) pair graded against a real final
score. Settlement (test_pick_settlement.py) builds on top of this."""

from __future__ import annotations

import pytest

from app.outcomes.grading import grade_outcome


def test_match_result():
    assert grade_outcome("Match Result", "Home Win", home_score=2, away_score=1) is True
    assert grade_outcome("Match Result", "Draw", home_score=2, away_score=1) is False
    assert grade_outcome("Match Result", "Away Win", home_score=1, away_score=1) is False
    assert grade_outcome("Match Result", "Draw", home_score=1, away_score=1) is True


def test_double_chance():
    assert grade_outcome("Double Chance", "Home/Draw", home_score=1, away_score=1) is True
    assert grade_outcome("Double Chance", "Home/Draw", home_score=0, away_score=2) is False
    assert grade_outcome("Double Chance", "Draw/Away", home_score=0, away_score=2) is True


def test_draw_no_bet_voids_on_a_draw():
    assert grade_outcome("Draw No Bet", "Home", home_score=2, away_score=0) is True
    assert grade_outcome("Draw No Bet", "Home", home_score=1, away_score=1) is None


def test_both_teams_to_score():
    assert grade_outcome("Both Teams To Score", "Yes", home_score=1, away_score=1) is True
    assert grade_outcome("Both Teams To Score", "No", home_score=1, away_score=0) is True
    assert grade_outcome("Both Teams To Score", "Yes", home_score=0, away_score=3) is False


def test_total_goals_over_under():
    assert grade_outcome("Total Goals 2.5", "Over 2.5", home_score=2, away_score=1) is True
    assert grade_outcome("Total Goals 2.5", "Under 2.5", home_score=2, away_score=1) is False
    assert grade_outcome("Total Goals 2.5", "Under 2.5", home_score=1, away_score=1) is True


def test_home_and_away_goals_lines():
    assert grade_outcome("Home Goals 1.5", "Over 1.5", home_score=2, away_score=0) is True
    assert grade_outcome("Away Goals 1.5", "Under 1.5", home_score=2, away_score=1) is True


def test_correct_score():
    assert grade_outcome("Correct Score", "2-1", home_score=2, away_score=1) is True
    assert grade_outcome("Correct Score", "2-1", home_score=1, away_score=2) is False


def test_clean_sheets():
    assert grade_outcome("Home Clean Sheet", "Yes", home_score=1, away_score=0) is True
    assert grade_outcome("Away Clean Sheet", "Yes", home_score=1, away_score=0) is False
    assert grade_outcome("Both Teams Clean Sheet", "Yes", home_score=0, away_score=0) is True


def test_odd_even():
    assert grade_outcome("Total Goals Odd/Even", "Even", home_score=1, away_score=1) is True
    assert grade_outcome("Total Goals Odd/Even", "Odd", home_score=2, away_score=1) is True
    assert grade_outcome("Home Goals Odd/Even", "Odd", home_score=1, away_score=0) is True


def test_ht_markets_need_ht_scores():
    assert grade_outcome("HT Result", "Home", home_score=2, away_score=1) is None
    assert (
        grade_outcome("HT Result", "Home", home_score=2, away_score=1, ht_home_score=1, ht_away_score=0) is True
    )
    assert (
        grade_outcome(
            "HT Double Chance", "Draw/Away", home_score=2, away_score=1, ht_home_score=0, ht_away_score=0
        )
        is True
    )
    assert (
        grade_outcome(
            "HT Total Goals 0.5", "Over 0.5", home_score=2, away_score=1, ht_home_score=1, ht_away_score=0
        )
        is True
    )
    assert (
        grade_outcome("HT Correct Score", "1-0", home_score=2, away_score=1, ht_home_score=1, ht_away_score=0)
        is True
    )
    assert (
        grade_outcome(
            "HT Both Teams To Score", "No", home_score=2, away_score=1, ht_home_score=1, ht_away_score=0
        )
        is True
    )


def test_unknown_market_is_ungradeable():
    assert grade_outcome("Winning Margin", "Home by 2", home_score=2, away_score=0) is None
    assert grade_outcome("Result & BTTS", "Home & BTTS Yes", home_score=2, away_score=1) is None


def test_second_half_total_goals_is_full_time_minus_half_time():
    # 3-1 final, 1-0 at the break: 2-1 in the second half, 3 goals.
    scores = dict(home_score=3, away_score=1, ht_home_score=1, ht_away_score=0)
    assert grade_outcome("2H Total Goals 2.5", "Over 2.5", **scores) is True
    assert grade_outcome("2H Total Goals 3.5", "Over 3.5", **scores) is False
    assert grade_outcome("2H Total Goals 3.5", "Under 3.5", **scores) is True
    # The full-time and first-half totals aren't confused with it.
    assert grade_outcome("HT Total Goals 1.5", "Over 1.5", **scores) is False


def test_second_half_total_goals_needs_the_half_time_score():
    assert grade_outcome("2H Total Goals 0.5", "Over 0.5", home_score=1, away_score=0) is None


def test_second_half_per_team_lines_are_not_graded_as_the_combined_total():
    scores = dict(home_score=3, away_score=1, ht_home_score=1, ht_away_score=0)
    assert grade_outcome("2H Home Goals 0.5", "Over 0.5", **scores) is None


def test_second_half_single_half_markets_use_full_time_less_half_time():
    # 3-1 final, 1-0 at the break: the second half finished 2-1 to the home side.
    scores = dict(home_score=3, away_score=1, ht_home_score=1, ht_away_score=0)
    assert grade_outcome("2H Result", "Home", **scores) is True
    assert grade_outcome("2H Result", "Draw", **scores) is False
    assert grade_outcome("2H Double Chance", "Draw/Away", **scores) is False
    assert grade_outcome("2H Both Teams To Score", "Yes", **scores) is True
    assert grade_outcome("2H Correct Score", "2-1", **scores) is True
    assert grade_outcome("HT Correct Score", "1-0", **scores) is True


def test_second_half_markets_need_the_half_time_score():
    assert grade_outcome("2H Result", "Home", home_score=1, away_score=0) is None
    assert grade_outcome("2H Correct Score", "1-0", home_score=1, away_score=0) is None


# --- SportyBet's "X or Y" markets: Yes wins on EITHER condition ------------------


def _grade(market, selection, home, away):
    return grade_outcome(market, selection, home_score=home, away_score=away)


@pytest.mark.parametrize("market, home, away, yes_wins", [
    ("Home Team or Over 2.5", 1, 0, True),    # home won, only 1 goal
    ("Home Team or Over 2.5", 0, 3, True),    # home lost, but 3 goals: the over wins it
    ("Home Team or Over 2.5", 1, 1, False),   # neither
    ("Home Team or Under 2.5", 0, 2, True),   # home lost, under 2.5
    ("Home Team or Under 2.5", 2, 1, True),   # home won (3 goals)
    ("Home Team or Under 2.5", 1, 3, False),
    ("Draw or Over 2.5", 1, 1, True),         # a 2-goal draw: the draw wins it
    ("Draw or Over 2.5", 3, 0, True),
    ("Draw or Over 2.5", 2, 0, False),
    ("Draw or Under 2.5", 2, 2, True),        # a 4-goal draw
    ("Draw or Under 2.5", 1, 0, True),
    ("Draw or Under 2.5", 3, 1, False),
    ("Away or Over 2.5", 0, 1, True),
    ("Away or Over 2.5", 2, 1, True),
    ("Away or Over 2.5", 1, 0, False),
    ("Away or Under 2.5", 1, 1, True),
    ("Away or Under 2.5", 3, 1, False),
    ("Home Team or GG", 1, 0, True),
    ("Home Team or GG", 1, 2, True),          # home lost but both scored
    ("Home Team or GG", 0, 1, False),
    ("Draw or GG", 0, 0, True),               # a goalless draw
    ("Draw or GG", 2, 1, True),               # both scored
    ("Draw or GG", 1, 0, False),
    ("Away Team or GG", 2, 1, True),
    ("Away Team or GG", 1, 0, False),
    ("Home Team or Any Clean Sheet", 2, 1, True),   # home won
    ("Home Team or Any Clean Sheet", 0, 1, True),   # lost, but home was kept out: a clean sheet for away
    ("Home Team or Any Clean Sheet", 1, 1, False),  # a scoring draw
    ("Draw or Any Clean Sheet", 0, 0, True),
    ("Draw or Any Clean Sheet", 2, 0, True),        # not a draw, but a clean sheet
    ("Draw or Any Clean Sheet", 2, 2, True),        # a draw
    ("Draw or Any Clean Sheet", 2, 1, False),
    ("Away Team or Any Clean Sheet", 1, 2, True),
    ("Away Team or Any Clean Sheet", 1, 0, True),   # away lost, home kept a clean sheet
    ("Away Team or Any Clean Sheet", 2, 2, False),
])
def test_or_markets_yes_wins_on_either_condition_and_no_is_the_opposite(market, home, away, yes_wins):
    assert _grade(market, "Yes", home, away) is yes_wins
    assert _grade(market, "No", home, away) is (not yes_wins)


def test_or_markets_with_an_unknown_selection_are_not_graded():
    assert _grade("Draw or GG", "Maybe", 1, 1) is None
    assert _grade("Home Team or Over 2.5", "Home or Over 2.5", 1, 1) is None


@pytest.mark.parametrize(
    "selection,ht,ft,expected",
    [
        ("Home", (1, 0), (1, 2), True),  # led at half-time, lost the match
        ("Away", (1, 0), (1, 2), True),  # won the match
        ("Draw", (1, 0), (1, 2), False),
        ("Draw", (0, 0), (2, 1), True),  # level at half-time
        ("Home", (0, 1), (0, 1), False),
        ("Draw", (1, 0), (1, 1), True),  # drawn at full time
    ],
)
def test_first_half_result_or_match_result(selection, ht, ft, expected):
    got = grade_outcome(
        "1st Half Result or Match Result", selection,
        home_score=ft[0], away_score=ft[1], ht_home_score=ht[0], ht_away_score=ht[1],
    )
    assert got is expected


def test_first_half_result_or_match_result_needs_the_half_time_score():
    assert grade_outcome("1st Half Result or Match Result", "Home", home_score=2, away_score=0) is None


@pytest.mark.parametrize(
    "market,selection,score,expected",
    [
        ("Home Win To Nil", "Yes", (2, 0), True),
        ("Home Win To Nil", "Yes", (2, 1), False),
        ("Home Win To Nil", "Yes", (0, 0), False),
        ("Home Win To Nil", "No", (0, 0), True),
        ("Away Win To Nil", "Yes", (0, 1), True),
        ("Away Win To Nil", "Yes", (1, 2), False),
        ("Away Win To Nil", "No", (3, 0), True),
    ],
)
def test_win_to_nil(market, selection, score, expected):
    assert grade_outcome(market, selection, home_score=score[0], away_score=score[1]) is expected
