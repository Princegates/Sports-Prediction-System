"""The Poisson model's league and team averages are computed in SQL.

They used to be computed in Python, over every finished match in the league,
fetched in full -- three times per prediction. That is how a backtest of one
league moved 439 MB and 3 million rows out of a 4.4 MB database, which a
managed Postgres bills as egress.

Moving arithmetic into the database is only safe if the numbers come out
identical, so these tests keep the original reduction as a reference
implementation and assert the two agree exactly. The edge cases matter as
much as the happy path: an empty league falls back to a fixed default, and a
team with no matches in one context must read as league-average rather than
zero -- ``AVG`` over no rows is NULL, not 0, and confusing the two would make
every newly promoted side look like it cannot score.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.db.models import Match, Team
from app.prediction_models.poisson_model import DEFAULT_HT_GOAL_FRACTION, ht_goal_fraction, league_goal_averages, team_attack_defense

LEAGUE = "Aggregate Test League"
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


def _add(db, home, away, day, home_score, away_score):
    db.add(
        Match(
            league=LEAGUE,
            season="2024-25",
            date=BASE + dt.timedelta(days=day),
            home_team_id=home.id,
            away_team_id=away.id,
            home_score=home_score,
            away_score=away_score,
            status="FINISHED",
        )
    )


# --- reference implementations: the original Python reductions -------------


def _reference_league_averages(matches: list[tuple]) -> tuple[float, float]:
    if not matches:
        return 1.45, 1.15
    return (
        sum(m[2] for m in matches) / len(matches),
        sum(m[3] for m in matches) / len(matches),
    )


def _reference_attack_defense(matches: list[tuple], team_id: int, avg_home: float, avg_away: float):
    home_matches = [m for m in matches if m[0] == team_id]
    away_matches = [m for m in matches if m[1] == team_id]

    def ratio(values: list[int], league_avg: float) -> float:
        if not values or league_avg <= 0:
            return 1.0
        return max((sum(values) / len(values)) / league_avg, 0.05)

    return (
        ratio([m[2] for m in home_matches], avg_home),
        ratio([m[3] for m in home_matches], avg_away),
        ratio([m[3] for m in away_matches], avg_away),
        ratio([m[2] for m in away_matches], avg_home),
    )


# --- tests ----------------------------------------------------------------


def test_league_averages_match_the_python_reduction(db_session, teams):
    a, b, g = teams["alpha"], teams["beta"], teams["gamma"]
    fixtures = [
        (a, b, 1, 3, 0),
        (b, a, 2, 1, 1),
        (a, g, 3, 2, 2),
        (g, b, 4, 0, 4),
        (b, g, 5, 2, 1),
        (g, a, 6, 1, 3),
    ]
    for home, away, day, hs, as_ in fixtures:
        _add(db_session, home, away, day, hs, as_)
    db_session.commit()

    rows = [(h.id, w.id, hs, as_) for h, w, _, hs, as_ in fixtures]
    expected = _reference_league_averages(rows)

    assert league_goal_averages(db_session, LEAGUE, AS_OF) == pytest.approx(expected)


def test_attack_defense_matches_the_python_reduction(db_session, teams):
    a, b, g = teams["alpha"], teams["beta"], teams["gamma"]
    fixtures = [
        (a, b, 1, 3, 0),
        (b, a, 2, 1, 1),
        (a, g, 3, 2, 2),
        (g, b, 4, 0, 4),
        (b, g, 5, 2, 1),
        (g, a, 6, 1, 3),
        (a, b, 7, 0, 0),
    ]
    for home, away, day, hs, as_ in fixtures:
        _add(db_session, home, away, day, hs, as_)
    db_session.commit()

    rows = [(h.id, w.id, hs, as_) for h, w, _, hs, as_ in fixtures]
    avg_home, avg_away = _reference_league_averages(rows)

    for team in (a, b, g):
        expected = _reference_attack_defense(rows, team.id, avg_home, avg_away)
        actual = team_attack_defense(db_session, team.id, LEAGUE, AS_OF, avg_home, avg_away)
        assert actual == pytest.approx(expected), f"mismatch for team {team.name}"


def test_only_matches_before_as_of_are_counted(db_session, teams):
    """The whole backtest rests on this: a figure for a match on the 10th
    must not see the 11th. Aggregating in SQL must not quietly widen the
    window."""

    a, b = teams["alpha"], teams["beta"]
    _add(db_session, a, b, 1, 5, 0)
    _add(db_session, b, a, 50, 0, 5)  # after the cutoff
    db_session.commit()

    cutoff = BASE + dt.timedelta(days=10)
    assert league_goal_averages(db_session, LEAGUE, cutoff) == pytest.approx((5.0, 0.0))


def test_unfinished_matches_are_ignored(db_session, teams):
    a, b, g = teams["alpha"], teams["beta"], teams["gamma"]
    _add(db_session, a, b, 1, 4, 2)
    db_session.add(
        Match(
            league=LEAGUE,
            season="2024-25",
            date=BASE + dt.timedelta(days=2),
            home_team_id=g.id,
            away_team_id=a.id,
            home_score=None,
            away_score=None,
            status="SCHEDULED",
        )
    )
    db_session.commit()

    assert league_goal_averages(db_session, LEAGUE, AS_OF) == pytest.approx((4.0, 2.0))


def test_empty_league_falls_back_to_the_default(db_session, teams):
    assert league_goal_averages(db_session, LEAGUE, AS_OF) == (1.45, 1.15)


def test_team_with_no_matches_in_a_context_reads_as_league_average(db_session, teams):
    """AVG over zero rows is NULL. Treating that as 0.0 would say the team
    never scores, which for a side that has only ever played away is both
    wrong and confidently so."""

    a, b, g = teams["alpha"], teams["beta"], teams["gamma"]
    _add(db_session, a, b, 1, 2, 1)
    _add(db_session, a, g, 2, 2, 1)
    db_session.commit()

    # Gamma has played away only -- its home attack/defense are unknown.
    home_attack, home_defense, away_attack, away_defense = team_attack_defense(
        db_session, g.id, LEAGUE, AS_OF, 2.0, 1.0
    )

    assert home_attack == 1.0
    assert home_defense == 1.0
    assert away_attack == pytest.approx(1.0)
    assert away_defense == pytest.approx(1.0)


def test_ratio_floor_is_preserved(db_session, teams):
    """A side that has been shut out every game still gets 0.05, not 0 --
    zero would make the Poisson matrix collapse."""

    a, b = teams["alpha"], teams["beta"]
    for day in range(4):
        _add(db_session, a, b, day, 0, 3)
    db_session.commit()

    home_attack, _, _, _ = team_attack_defense(db_session, a.id, LEAGUE, AS_OF, 2.0, 1.0)
    assert home_attack == 0.05


# --- half-time goal-rate split ----------------------------------------------


def _add_with_ht(db, home, away, day, hs, as_, ht_hs, ht_as):
    db.add(
        Match(
            league=LEAGUE, season="2024-25", date=BASE + dt.timedelta(days=day),
            home_team_id=home.id, away_team_id=away.id,
            home_score=hs, away_score=as_, ht_home_score=ht_hs, ht_away_score=ht_as,
            status="FINISHED",
        )
    )


def test_ht_goal_fraction_is_fit_from_recorded_half_time_scores(db_session, teams):
    a, b = teams["alpha"], teams["beta"]
    # 10 total goals, 4 of them by half-time -- a clean, unambiguous 0.4 split.
    _add_with_ht(db_session, a, b, 1, 3, 1, 1, 1)  # 4 FT, 2 HT
    _add_with_ht(db_session, b, a, 2, 2, 4, 1, 1)  # 6 FT, 2 HT

    db_session.commit()
    assert ht_goal_fraction(db_session, LEAGUE, AS_OF) == pytest.approx(4 / 10)


def test_ht_goal_fraction_falls_back_to_the_default_with_no_recorded_ht_scores(db_session, teams):
    a, b = teams["alpha"], teams["beta"]
    _add(db_session, a, b, 1, 2, 1)  # no ht_home_score/ht_away_score set
    db_session.commit()

    assert ht_goal_fraction(db_session, LEAGUE, AS_OF) == DEFAULT_HT_GOAL_FRACTION


def test_ht_goal_fraction_ignores_matches_missing_either_half_time_score(db_session, teams):
    """A match with only one of the two HT columns recorded is excluded
    entirely, not treated as 0 for the missing side -- half a data-entry
    error is still an error."""

    a, b = teams["alpha"], teams["beta"]
    _add_with_ht(db_session, a, b, 1, 3, 1, 1, 1)  # complete: 4 FT, 2 HT
    db_session.add(
        Match(
            league=LEAGUE, season="2024-25", date=BASE + dt.timedelta(days=2),
            home_team_id=b.id, away_team_id=a.id,
            home_score=5, away_score=5, ht_home_score=3, ht_away_score=None,
            status="FINISHED",
        )
    )
    db_session.commit()

    assert ht_goal_fraction(db_session, LEAGUE, AS_OF) == pytest.approx(2 / 4)


def test_ht_goal_fraction_is_clamped_to_a_plausible_range(db_session, teams):
    a, b = teams["alpha"], teams["beta"]
    # Every goal arrived by half-time -- implausible as a league-wide split,
    # must not be taken at face value.
    _add_with_ht(db_session, a, b, 1, 4, 0, 4, 0)
    db_session.commit()

    assert ht_goal_fraction(db_session, LEAGUE, AS_OF) == 0.65


def test_ht_goal_fraction_only_considers_matches_before_as_of(db_session, teams):
    a, b = teams["alpha"], teams["beta"]
    _add_with_ht(db_session, a, b, 1, 4, 0, 2, 0)  # 0.5 split, before cutoff
    _add_with_ht(db_session, b, a, 50, 4, 0, 0, 0)  # 0.0 split, after cutoff

    db_session.commit()
    cutoff = BASE + dt.timedelta(days=10)
    assert ht_goal_fraction(db_session, LEAGUE, cutoff) == pytest.approx(0.5)
