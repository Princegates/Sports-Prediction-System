"""The Poisson ratings are fitted jointly, so they adjust for opponents.

The old ratio ratings divided a team's goals by the league average, which
credits a side for a soft run of fixtures. These tests pin the behaviour the
joint fit exists for, plus the cache that keeps it from refetching a league's
history for every prediction.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from app.db.models import Match, Team
from app.prediction_models.poisson_model import (
    clear_rating_cache,
    expected_goals,
    fit_ratings_from_rows,
    fit_team_ratings,
)

LEAGUE = "Ratings Test League"
BASE = dt.datetime(2025, 1, 1, 15, 0)
AS_OF = BASE + dt.timedelta(days=200)


@pytest.fixture(autouse=True)
def _fresh_cache():
    clear_rating_cache()
    yield
    clear_rating_cache()


def _rows(matches):
    """``matches`` is (days_before, home, away, home_goals, away_goals)."""

    arr = np.array(matches, dtype=float)
    return arr[:, 0], arr[:, 1].astype(int), arr[:, 2].astype(int), arr[:, 3], arr[:, 4]


def test_the_same_scorelines_rate_higher_against_stronger_opponents():
    # Teams 1 and 2 score 2-1 at home every time. Team 1 does it against
    # team 3, which beats everyone else; team 2 against team 4, which loses
    # to everyone else. A ratio rating sees two identical records.
    matches = []
    day = 1
    for _ in range(6):
        matches += [
            (day, 1, 3, 2, 1),
            (day, 2, 4, 2, 1),
            (day + 1, 3, 5, 3, 0),
            (day + 1, 6, 3, 0, 3),
            (day + 2, 4, 5, 0, 2),
            (day + 2, 6, 4, 2, 0),
        ]
        day += 7
    ratings = fit_ratings_from_rows(*_rows(matches))

    assert ratings.attack[1] > ratings.attack[2]
    assert ratings.defence[1] < ratings.defence[2]


def test_a_team_with_no_history_reads_as_league_average():
    ratings = fit_ratings_from_rows(*_rows([(1, 1, 2, 3, 0), (2, 2, 1, 1, 1)]))
    home, away = ratings.lambdas(98, 99)
    assert home == pytest.approx(np.exp(ratings.intercept + ratings.home_advantage))
    assert away == pytest.approx(np.exp(ratings.intercept))


def _background(teams=(3, 4, 5, 6)):
    """A round robin of 1-1 draws, so the intercept and home advantage are
    pinned by matches other than the ones under test."""

    return [(d, h, a, 1, 1) for d in (10, 20, 30) for h in teams for a in teams if h != a]


def test_the_penalty_pulls_a_short_record_toward_average():
    record = _rows(_background() + [(1, 1, 3, 5, 0), (2, 4, 1, 0, 1)])
    loose = fit_ratings_from_rows(*record, penalty=0.5)
    tight = fit_ratings_from_rows(*record, penalty=20.0)
    assert 0 < tight.attack[1] < loose.attack[1]


def test_recent_matches_count_for_more():
    # Team 1 lost heavily long ago and wins heavily now; team 2 the reverse.
    matches = [(900, 1, 3, 0, 4), (900, 3, 2, 0, 4), (5, 1, 3, 4, 0), (5, 3, 2, 4, 0)]
    ratings = fit_ratings_from_rows(*_rows(matches), penalty=1.0)
    assert ratings.attack[1] > ratings.attack[2]


def test_empty_history_returns_none():
    assert fit_ratings_from_rows(*[np.array([])] * 5) is None


@pytest.fixture()
def teams(db_session):
    made = []
    for name in ("Alpha", "Beta", "Gamma"):
        t = Team(name=f"{name} FC", league=LEAGUE, aliases=[])
        db_session.add(t)
        made.append(t)
    db_session.commit()
    return made


def _add(db, home, away, day, hs, as_):
    db.add(Match(
        league=LEAGUE, season="2024-25", date=BASE + dt.timedelta(days=day),
        home_team_id=home.id, away_team_id=away.id, home_score=hs, away_score=as_, status="FINISHED",
    ))


def test_matches_on_or_after_as_of_are_not_read(db_session, teams):
    a, b, g = teams
    for day in range(0, 120, 7):
        _add(db_session, a, b, day, 1, 1)
        _add(db_session, b, g, day + 1, 1, 1)
    db_session.commit()
    before = expected_goals(db_session, a.id, g.id, LEAGUE, AS_OF)

    _add(db_session, a, g, 200, 9, 0)  # kicks off exactly at AS_OF
    _add(db_session, a, g, 250, 9, 0)
    db_session.commit()
    assert expected_goals(db_session, a.id, g.id, LEAGUE, AS_OF) == pytest.approx(before)


def test_a_new_result_invalidates_the_cached_fit(db_session, teams):
    a, b, g = teams
    for day in range(0, 120, 7):
        _add(db_session, a, b, day, 1, 1)
        _add(db_session, b, g, day + 1, 1, 1)
    db_session.commit()
    first = fit_team_ratings(db_session, LEAGUE, AS_OF)
    assert fit_team_ratings(db_session, LEAGUE, AS_OF) is first

    _add(db_session, a, g, 150, 6, 0)
    db_session.commit()
    refreshed = fit_team_ratings(db_session, LEAGUE, AS_OF)
    assert refreshed is not first
    assert refreshed.attack[a.id] > first.attack[a.id]


def test_a_corrected_score_invalidates_the_cached_fit(db_session, teams):
    a, b, g = teams
    for day in range(0, 120, 7):
        for home, away in ((a, b), (b, g), (g, a), (b, a), (g, b), (a, g)):
            _add(db_session, home, away, day, 1, 1)
    db_session.commit()
    first = fit_team_ratings(db_session, LEAGUE, AS_OF)

    match = db_session.query(Match).filter(Match.home_team_id == a.id).order_by(Match.date.desc()).first()
    match.home_score = 5
    db_session.commit()
    assert fit_team_ratings(db_session, LEAGUE, AS_OF).attack[a.id] > first.attack[a.id]
