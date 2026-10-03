"""Plain-language factor generation (app.explain.generate_explanation),
focused on the squad-absence factor -- the rest of the function's behavior
is exercised indirectly wherever a Prediction gets built.
"""

from __future__ import annotations

from app.explain import generate_explanation
from app.features.squad_strength import AbsenceImpact
from app.features.team_stats import TeamForm


def _form() -> TeamForm:
    return TeamForm(matches_played=10, points_per_game=1.5, rest_days=7.0)


class TestAbsenceFactor:
    def test_no_factor_with_no_impact_given(self):
        explanation = generate_explanation("Home FC", "Away FC", _form(), _form(), elo_diff=60.0, home_advantage=60.0)
        assert not any("missing" in line for line in explanation["positive"] + explanation["negative"])

    def test_a_small_absence_is_not_worth_mentioning(self):
        """Below the 0.3 strength-lost threshold: squad depth, not news."""

        explanation = generate_explanation(
            "Home FC", "Away FC", _form(), _form(), elo_diff=60.0, home_advantage=60.0,
            home_absences=AbsenceImpact(missing_players=["Fringe Player"], strength_lost=0.2),
        )
        assert not any("missing" in line for line in explanation["positive"] + explanation["negative"])

    def test_the_home_teams_own_absence_favors_the_away_side(self):
        explanation = generate_explanation(
            "Home FC", "Away FC", _form(), _form(), elo_diff=60.0, home_advantage=60.0,
            home_absences=AbsenceImpact(missing_players=["Star Striker"], strength_lost=0.9),
        )
        assert any("Home FC missing Star Striker" in line for line in explanation["negative"])
        assert not any("missing" in line for line in explanation["positive"])

    def test_the_away_teams_own_absence_favors_the_home_side(self):
        explanation = generate_explanation(
            "Home FC", "Away FC", _form(), _form(), elo_diff=60.0, home_advantage=60.0,
            away_absences=AbsenceImpact(missing_players=["Star Striker"], strength_lost=0.9),
        )
        assert any("Away FC missing Star Striker" in line for line in explanation["positive"])
        assert not any("missing" in line for line in explanation["negative"])

    def test_more_than_two_missing_players_are_summarized(self):
        explanation = generate_explanation(
            "Home FC", "Away FC", _form(), _form(), elo_diff=60.0, home_advantage=60.0,
            away_absences=AbsenceImpact(missing_players=["A", "B", "C"], strength_lost=0.9),
        )
        line = next(line for line in explanation["positive"] if "missing" in line)
        assert "A, B and 1 other" in line

    def test_both_teams_can_have_an_absence_factor_at_once(self):
        explanation = generate_explanation(
            "Home FC", "Away FC", _form(), _form(), elo_diff=60.0, home_advantage=60.0,
            home_absences=AbsenceImpact(missing_players=["Home Star"], strength_lost=0.9),
            away_absences=AbsenceImpact(missing_players=["Away Star"], strength_lost=0.9),
        )
        assert any("Home FC missing Home Star" in line for line in explanation["negative"])
        assert any("Away FC missing Away Star" in line for line in explanation["positive"])


class TestReturnShape:
    def test_always_returns_the_two_bucket_dict_shape(self):
        """Regression guard for a real bug: prediction_service.py used to
        string-format the calibration note directly onto this return value
        (f"{explanation} ...") whenever cross-league calibration applied,
        turning the dict into its str() repr before it ever reached the
        database -- this function's own contract must stay a plain
        dict[str, list[str]] regardless of what a caller does with it."""

        explanation = generate_explanation("Home FC", "Away FC", _form(), _form(), elo_diff=60.0, home_advantage=60.0)
        assert set(explanation.keys()) == {"positive", "negative"}
        assert isinstance(explanation["positive"], list)
        assert isinstance(explanation["negative"], list)
