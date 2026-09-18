"""Guards against reloading a league's history once per match.

This is the bug that cost real money. ``build_feature_row`` constructed a
``LeagueFeatureCache`` internally and discarded it, so every caller that
predicted matches in a loop -- the backtester, the prediction scripts, the
endpoints that fill in a day's fixtures -- reloaded the entire league for
each one. A single-league backtest read 3,004,247 rows out of a database
holding 29,000, and 439 MB left the database for one league's nightly run.
A managed Postgres bills that as egress, and the monthly allowance went in
two nights.

Nothing about it was visible in the tests: the numbers were all correct.
Only the volume was wrong, so the guard has to be about volume.

The assertions here are deliberately loose. The exact query count will drift
as features are added, and a test that pins it would just get bumped each
time without anyone reading it. What must not drift is the *shape*: the cost
of the shared-cache path stays flat as matches are added, and the
no-cache path does not.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import event

from app.db.models import Match, Team
from app.db.session import engine
from app.prediction_models import elo, ml_model as ml_model_module
from app.prediction_models.ml_model import FeatureCachePool, LeagueFeatureCache, MLPrediction
from app import prediction_service
from app.prediction_service import build_prediction_for_match

LEAGUE = "Budget Test League"
BASE = dt.datetime(2024, 1, 1, 15, 0)


@pytest.fixture()
def seeded_league(db_session):
    """A league with enough history that reloading it is measurably wasteful."""

    teams = []
    for name in ("Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta"):
        t = Team(name=f"{name} FC", league=LEAGUE, aliases=[])
        db_session.add(t)
        teams.append(t)
    db_session.commit()
    for t in teams:
        db_session.refresh(t)

    day = 0
    for home in teams:
        for away in teams:
            if home.id == away.id:
                continue
            db_session.add(
                Match(
                    league=LEAGUE,
                    season="2023-24",
                    date=BASE + dt.timedelta(days=day),
                    home_team_id=home.id,
                    away_team_id=away.id,
                    home_score=(day % 3),
                    away_score=(day % 2),
                    status="FINISHED",
                )
            )
            day += 1
    db_session.commit()
    elo.rebuild_elo_history(db_session, LEAGUE)

    upcoming = []
    for i in range(6):
        m = Match(
            league=LEAGUE,
            season="2024-25",
            date=BASE + dt.timedelta(days=400 + i),
            home_team_id=teams[i % len(teams)].id,
            away_team_id=teams[(i + 1) % len(teams)].id,
            status="SCHEDULED",
        )
        db_session.add(m)
        upcoming.append(m)
    db_session.commit()
    for m in upcoming:
        db_session.refresh(m)

    return upcoming



class _StubMLModel:
    """A stand-in trained model.

    The feature cache is only touched when an ML model exists, so without
    this the tests below would exercise the Elo and Poisson paths and report
    a cache count of zero -- passing while measuring nothing. The first
    version of this file did exactly that, which is why the negative test
    next to each guard is not optional.
    """

    def predict(self, feature_row: dict[str, float]) -> MLPrediction:
        return MLPrediction(home_win=0.45, draw=0.3, away_win=0.25, over_2_5=0.5, btts_yes=0.5)


@pytest.fixture(autouse=True)
def _trained_model(monkeypatch):
    monkeypatch.setattr(prediction_service, "load_ml_model", lambda league: _StubMLModel())


class _Counter:
    """Counts SQL statements and cache constructions over a block."""

    def __init__(self, monkeypatch):
        self.statements = 0
        self.caches_built = 0
        real_init = LeagueFeatureCache.__init__

        def counting_init(cache_self, *args, **kwargs):
            self.caches_built += 1
            return real_init(cache_self, *args, **kwargs)

        monkeypatch.setattr(ml_model_module.LeagueFeatureCache, "__init__", counting_init)

    def _on_execute(self, *_args, **_kwargs):
        self.statements += 1

    def __enter__(self):
        event.listen(engine, "before_cursor_execute", self._on_execute)
        return self

    def __exit__(self, *_exc):
        event.remove(engine, "before_cursor_execute", self._on_execute)
        return False


def test_shared_cache_is_built_once_for_a_whole_batch(db_session, seeded_league, monkeypatch):
    counter = _Counter(monkeypatch)
    with counter:
        pool = FeatureCachePool(db_session)
        for match in seeded_league:
            build_prediction_for_match(db_session, match, feature_cache=pool.for_league(match.league))

    assert counter.caches_built == 1, (
        f"expected one cache for {len(seeded_league)} matches in one league, "
        f"got {counter.caches_built}"
    )


def test_without_a_cache_every_match_reloads_the_league(db_session, seeded_league, monkeypatch):
    """The behaviour being guarded against, asserted directly so the guard
    above can't pass vacuously."""

    counter = _Counter(monkeypatch)
    with counter:
        for match in seeded_league:
            build_prediction_for_match(db_session, match)

    assert counter.caches_built == len(seeded_league)


def test_batch_cost_stays_flat_as_matches_are_added(db_session, seeded_league, monkeypatch):
    """The shape that matters: with a shared cache, predicting twice as many
    matches must not cost twice the queries against league history."""

    def statements_for(matches):
        counter = _Counter(monkeypatch)
        with counter:
            pool = FeatureCachePool(db_session)
            for match in matches:
                build_prediction_for_match(db_session, match, feature_cache=pool.for_league(match.league))
        return counter.statements

    few = statements_for(seeded_league[:2])
    many = statements_for(seeded_league)

    # Per-match work (writing the prediction, reading the two teams) is
    # unavoidable and grows; reloading the league is not and must not.
    per_match = (many - few) / (len(seeded_league) - 2)
    assert per_match < few, (
        f"each extra match costs {per_match:.1f} statements, which is not flat -- "
        "the league history is probably being reloaded per match again"
    )
