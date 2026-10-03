"""grade_outcome: a (market, selection) pair graded against a real final
score. Settlement (test_pick_settlement.py) builds on top of this."""

from __future__ import annotations

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
