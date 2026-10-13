"""Corners model: the independent-Poisson matrix (mirrors test_poisson.py),
and the "no corners data means no guess" contract that's the whole point of
the ``None``-returning functions in app.prediction_models.corners_model
(mirrors test_poisson_aggregates.py, scoped to the one behavior that differs
from the goals model -- everything else reuses poisson_model's own tested
decay/shrinkage arithmetic via decay_weight_expr).
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.db.models import Match, Team
from app.prediction_models.corners_model import (
    build_corners_matrix,
    expected_corners,
    league_corners_averages,
    markets_from_corners_matrix,
    predict,
    team_corners_attack_defense,
)

LEAGUE = "Corners Test League"
BASE = dt.datetime(2025, 1, 1, 15, 0)
AS_OF = BASE + dt.timedelta(days=365)


@pytest.fixture()
def teams(db_session):
    made = {}
    for name in ("Alpha", "Beta", "Gamma"):
        t = Team(name=f"{name} FC", league=LEAGUE, aliases=[])
        db_session.add(t)
        made[name.lower()] = t
    db_session.commit()
    for t in made.values():
        db_session.refresh(t)
    return made


def _add(db, home, away, day, home_score, away_score, home_corners=None, away_corners=None):
    db.add(
        Match(
            league=LEAGUE,
            season="2024-25",
            date=BASE + dt.timedelta(days=day),
            home_team_id=home.id,
            away_team_id=away.id,
            home_score=home_score,
            away_score=away_score,
            home_corners=home_corners,
            away_corners=away_corners,
            status="FINISHED",
        )
    )


# --- matrix / markets (pure arithmetic, no DB) ------------------------------


def test_corners_matrix_sums_to_one():
    matrix = build_corners_matrix(5.5, 4.5)
    total = sum(sum(row) for row in matrix)
    assert abs(total - 1.0) < 1e-6


def test_corners_markets_are_internally_consistent():
    matrix = build_corners_matrix(6.5, 4.0)
    markets = markets_from_corners_matrix(matrix, 6.5, 4.0)

    assert abs(markets.home_most_corners + markets.away_most_corners + markets.equal_corners - 1.0) < 1e-6
    # A side expected to win far more corners should be favored for "most corners".
    assert markets.home_most_corners > markets.away_most_corners

    lines = ["7.5", "8.5", "9.5", "10.5", "11.5"]
    values = [markets.over_probabilities[line] for line in lines]
    assert all(a >= b for a, b in zip(values, values[1:]))


def test_higher_corners_expectation_favors_higher_totals():
    low = markets_from_corners_matrix(build_corners_matrix(3.0, 3.0), 3.0, 3.0)
    high = markets_from_corners_matrix(build_corners_matrix(7.0, 6.0), 7.0, 6.0)
    assert high.over_probabilities["9.5"] > low.over_probabilities["9.5"]


# --- "no corners history means no guess" ------------------------------------


def test_league_with_no_corners_history_returns_none(db_session, teams):
    a, b = teams["alpha"], teams["beta"]
    # A finished match with a real score but no corners recorded -- exactly
    # what every league football-data.co.uk hasn't been run for looks like.
    _add(db_session, a, b, 1, 2, 1, home_corners=None, away_corners=None)
    db_session.commit()

    assert league_corners_averages(db_session, LEAGUE, AS_OF) is None
    assert expected_corners(db_session, a.id, b.id, LEAGUE, AS_OF) is None
    assert predict(db_session, a.id, b.id, LEAGUE, AS_OF) is None


def test_league_with_corners_history_returns_real_rates(db_session, teams):
    a, b, g = teams["alpha"], teams["beta"], teams["gamma"]
    _add(db_session, a, b, 1, 2, 1, home_corners=7, away_corners=3)
    _add(db_session, b, a, 2, 1, 1, home_corners=5, away_corners=5)
    _add(db_session, a, g, 3, 1, 0, home_corners=6, away_corners=4)
    db_session.commit()

    averages = league_corners_averages(db_session, LEAGUE, AS_OF)
    assert averages is not None
    avg_home, avg_away = averages
    assert avg_home > 0 and avg_away > 0

    expected = expected_corners(db_session, a.id, b.id, LEAGUE, AS_OF)
    assert expected is not None
    lambda_home, lambda_away = expected
    assert lambda_home > 0 and lambda_away > 0

    markets = predict(db_session, a.id, b.id, LEAGUE, AS_OF)
    assert markets is not None
    assert abs(markets.home_most_corners + markets.away_most_corners + markets.equal_corners - 1.0) < 1e-6


def test_a_team_with_no_corners_history_reads_as_league_average(db_session, teams):
    a, b, g = teams["alpha"], teams["beta"], teams["gamma"]
    _add(db_session, a, b, 1, 2, 1, home_corners=7, away_corners=3)
    _add(db_session, b, a, 2, 1, 1, home_corners=5, away_corners=5)
    db_session.commit()

    # Gamma has no corners history at all -- must shrink to league average,
    # not read as zero.
    home_attack, home_defense, away_attack, away_defense = team_corners_attack_defense(
        db_session, g.id, LEAGUE, AS_OF, 6.0, 4.0
    )
    assert home_attack == pytest.approx(1.0)
    assert home_defense == pytest.approx(1.0)
    assert away_attack == pytest.approx(1.0)
    assert away_defense == pytest.approx(1.0)


def test_only_matches_before_as_of_count_toward_corners_averages(db_session, teams):
    a, b = teams["alpha"], teams["beta"]
    _add(db_session, a, b, 1, 2, 0, home_corners=8, away_corners=2)
    _add(db_session, b, a, 50, 0, 2, home_corners=2, away_corners=8)  # after the cutoff
    db_session.commit()

    cutoff = BASE + dt.timedelta(days=10)
    averages = league_corners_averages(db_session, LEAGUE, cutoff)
    assert averages == pytest.approx((8.0, 2.0))
