"""Tests for the shot-based model features.

Two properties matter more than the arithmetic:

- **No leakage.** A feature for a match on the 10th must read only matches
  before the 10th. Leakage is invisible in production and shows up as a
  backtest that looks far better than the model really is.
- **Graceful absence.** Most matches have no statistics until the enrichment
  script runs, and some leagues never will. Missing statistics must produce a
  flagged-absent zero, never a fabricated average.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.prediction_models.ml_model import (
    FEATURE_COLUMNS,
    STATS_WINDOW,
    LeagueFeatureCache,
    build_feature_row,
)
from app.db.models import Match, Team

LEAGUE = "Test League"
BASE = dt.datetime(2025, 1, 1, 15, 0)


@pytest.fixture()
def league(db_session):
    home = Team(name="Alpha FC", league=LEAGUE, aliases=[])
    away = Team(name="Beta FC", league=LEAGUE, aliases=[])
    other = Team(name="Gamma FC", league=LEAGUE, aliases=[])
    db_session.add_all([home, away, other])
    db_session.commit()
    for t in (home, away, other):
        db_session.refresh(t)
    return {"home": home, "away": away, "other": other}


def _add_match(db, home, away, day, *, hs=None, as_=None, hst=None, ast=None, score=(1, 0)):
    m = Match(
        league=LEAGUE, season="2024-25", date=BASE + dt.timedelta(days=day),
        home_team_id=home.id, away_team_id=away.id,
        home_score=score[0], away_score=score[1], status="FINISHED",
        home_shots=hs, away_shots=as_, home_shots_on_target=hst, away_shots_on_target=ast,
    )
    db.add(m)
    db.commit()
    return m


# --- Absence -------------------------------------------------------------


def test_no_statistics_yields_flagged_zeros(db_session, league):
    """The state of every match before enrichment runs."""

    _add_match(db_session, league["home"], league["away"], 1)
    cache = LeagueFeatureCache(db_session, LEAGUE)

    stats = cache.shot_stats(league["home"].id, league["away"].id, BASE + dt.timedelta(days=10))

    assert stats["stats_available"] == 0.0
    assert stats["shots_for_diff"] == 0.0
    assert stats["sot_for_diff"] == 0.0


def test_one_sided_statistics_are_not_compared(db_session, league):
    """If only one side has statistics, the difference would compare a real
    average against an assumed one. Better to report nothing."""

    _add_match(db_session, league["home"], league["other"], 1, hs=15, as_=8, hst=6, ast=3)
    # Beta plays but with no stats recorded.
    _add_match(db_session, league["away"], league["other"], 2)

    cache = LeagueFeatureCache(db_session, LEAGUE)
    stats = cache.shot_stats(league["home"].id, league["away"].id, BASE + dt.timedelta(days=10))

    assert stats["stats_available"] == 0.0
    assert stats["shots_for_diff"] == 0.0


def test_feature_row_is_complete_without_statistics(db_session, league):
    """Every declared feature must be present even with no stats at all,
    or the model gets a differently-shaped row than it trained on."""

    _add_match(db_session, league["home"], league["away"], 1)
    row = build_feature_row(db_session, league["home"].id, league["away"].id, LEAGUE, BASE + dt.timedelta(days=10))

    assert set(row) == set(FEATURE_COLUMNS)


# --- Arithmetic ----------------------------------------------------------


def test_shot_rates_average_over_prior_matches(db_session, league):
    _add_match(db_session, league["home"], league["other"], 1, hs=10, as_=5, hst=4, ast=2)
    _add_match(db_session, league["home"], league["other"], 2, hs=20, as_=9, hst=8, ast=4)

    cache = LeagueFeatureCache(db_session, LEAGUE)
    rates = cache.shot_rates(league["home"].id, BASE + dt.timedelta(days=5))

    shots_for, shots_against, sot_for, sot_against = rates
    assert shots_for == pytest.approx(15.0)      # (10 + 20) / 2
    assert shots_against == pytest.approx(7.0)   # (5 + 9) / 2
    assert sot_for == pytest.approx(6.0)
    assert sot_against == pytest.approx(3.0)


def test_away_matches_count_from_the_right_side(db_session, league):
    """Playing away, the team's shots are the *away* column."""

    _add_match(db_session, league["other"], league["home"], 1, hs=4, as_=16, hst=1, ast=7)

    cache = LeagueFeatureCache(db_session, LEAGUE)
    shots_for, shots_against, sot_for, sot_against = cache.shot_rates(
        league["home"].id, BASE + dt.timedelta(days=5)
    )

    assert shots_for == pytest.approx(16.0), "read the home column for an away match"
    assert shots_against == pytest.approx(4.0)
    assert sot_for == pytest.approx(7.0)


def test_difference_is_home_minus_away(db_session, league):
    _add_match(db_session, league["home"], league["other"], 1, hs=18, as_=6, hst=7, ast=2)
    _add_match(db_session, league["away"], league["other"], 2, hs=8, as_=12, hst=3, ast=5)

    cache = LeagueFeatureCache(db_session, LEAGUE)
    stats = cache.shot_stats(league["home"].id, league["away"].id, BASE + dt.timedelta(days=5))

    assert stats["stats_available"] == 1.0
    assert stats["shots_for_diff"] == pytest.approx(18.0 - 8.0)
    assert stats["shots_against_diff"] == pytest.approx(6.0 - 12.0)
    assert stats["sot_for_diff"] == pytest.approx(7.0 - 3.0)


def test_matches_without_stats_are_skipped_not_counted_as_zero(db_session, league):
    """A match with no recorded shots must not drag the average toward zero."""

    _add_match(db_session, league["home"], league["other"], 1, hs=20, as_=10, hst=8, ast=4)
    _add_match(db_session, league["home"], league["other"], 2)  # no stats

    cache = LeagueFeatureCache(db_session, LEAGUE)
    shots_for, _, _, _ = cache.shot_rates(league["home"].id, BASE + dt.timedelta(days=5))

    assert shots_for == pytest.approx(20.0), "a stat-less match was averaged in as zero"


def test_window_limits_how_far_back_it_reads(db_session, league):
    """Only the most recent STATS_WINDOW matches count, so a team's current
    level isn't diluted by seasons-old form."""

    for day in range(STATS_WINDOW + 4):
        shots = 30 if day < 4 else 10          # oldest four are the outliers
        _add_match(db_session, league["home"], league["other"], day + 1, hs=shots, as_=5, hst=3, ast=2)

    cache = LeagueFeatureCache(db_session, LEAGUE)
    shots_for, _, _, _ = cache.shot_rates(league["home"].id, BASE + dt.timedelta(days=100))

    assert shots_for == pytest.approx(10.0), "read beyond the window"


# --- Leakage -------------------------------------------------------------


def test_future_matches_are_not_read(db_session, league):
    """The property the whole backtest rests on: a feature computed for a
    match must not see that match, or anything after it."""

    _add_match(db_session, league["home"], league["other"], 1, hs=10, as_=5, hst=4, ast=2)
    # A wildly different future match that must be invisible.
    _add_match(db_session, league["home"], league["other"], 20, hs=99, as_=99, hst=99, ast=99)

    cache = LeagueFeatureCache(db_session, LEAGUE)
    shots_for, _, _, _ = cache.shot_rates(league["home"].id, BASE + dt.timedelta(days=5))

    assert shots_for == pytest.approx(10.0), "a future match leaked into the features"


def test_the_match_itself_is_not_read(db_session, league):
    """Computed at exactly kickoff, the match being predicted is in the
    future by a hair and must be excluded."""

    kickoff = BASE + dt.timedelta(days=3)
    _add_match(db_session, league["home"], league["other"], 1, hs=10, as_=5, hst=4, ast=2)
    _add_match(db_session, league["home"], league["away"], 3, hs=50, as_=50, hst=50, ast=50)

    cache = LeagueFeatureCache(db_session, LEAGUE)
    shots_for, _, _, _ = cache.shot_rates(league["home"].id, kickoff)

    assert shots_for == pytest.approx(10.0), "the match being predicted leaked into its own features"
