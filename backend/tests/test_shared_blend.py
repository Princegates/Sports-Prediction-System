"""One set of blend weights for every league, and no 1X2 calibrator.

Both used to be fitted per league on ~600 validation matches. Scored on six
leagues' held-out test slices, a shared set of weights beat the per-league
ones in every league -- even a shared set fitted without that league -- and
dropping the per-league 1X2 calibrator improved log loss further. These tests
pin the serving side (what gets loaded) and the training side (what one
multi-league backtest fits and writes), because the failure mode is the
quiet one: stale per-league files on disk winning over the shared fit.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
from pathlib import Path

import pytest

from app import model_store
from app.config import get_settings
from app.db.models import Match, Team
from app.model_store import (
    GLOBAL_MODEL_KEY,
    calibrator_path,
    ensemble_weights_path,
    load_calibrators,
    load_ensemble_weights,
    ml_model_path,
    save_ensemble_weights,
)
from app.prediction_models.calibration import MarketCalibrator
from app.prediction_models.ensemble import EnsembleWeights

LEAGUE = "English Premier League"


@pytest.fixture()
def model_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(model_store, "MODEL_DIR", tmp_path)
    return tmp_path


def _load_backtest():
    path = Path(__file__).resolve().parents[1] / "scripts" / "backtest.py"
    spec = importlib.util.spec_from_file_location("backtest_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- serving ---------------------------------------------------------------


def test_the_shared_weights_serve_every_league(model_dir):
    save_ensemble_weights(EnsembleWeights(elo=0.5, poisson=0.3, ml=0.2))

    for league in (LEAGUE, "Spanish La Liga", "Some Newly Added League"):
        assert load_ensemble_weights(league) == EnsembleWeights(elo=0.5, poisson=0.3, ml=0.2)


def test_a_stale_per_league_weights_file_is_ignored(model_dir):
    """What an older backtest left behind on a deployment's disk. It must not
    quietly override the shared fit."""

    ensemble_weights_path(LEAGUE).write_text(json.dumps({"elo": 0.6, "poisson": 0.0, "ml": 0.4}))
    save_ensemble_weights(EnsembleWeights(elo=0.4, poisson=0.4, ml=0.2))

    assert load_ensemble_weights(LEAGUE) == EnsembleWeights(elo=0.4, poisson=0.4, ml=0.2)


def test_weights_fall_back_to_settings_before_any_fit(model_dir):
    ensemble_weights_path(LEAGUE).write_text(json.dumps({"elo": 0.6, "poisson": 0.0, "ml": 0.4}))
    settings = get_settings()

    assert load_ensemble_weights(LEAGUE) == EnsembleWeights(
        elo=settings.ensemble_weight_elo, poisson=settings.ensemble_weight_poisson, ml=settings.ensemble_weight_ml
    )


def test_a_stale_1x2_calibrator_is_not_loaded(model_dir):
    import numpy as np

    cal = MarketCalibrator()
    cal.fit({"yes": np.array([0.2, 0.8, 0.4, 0.6])}, {"yes": np.array([0, 1, 0, 1])})
    for market in ("1x2", "over_2_5"):
        cal.save(calibrator_path(LEAGUE, market))

    assert set(load_calibrators(LEAGUE)) == {"over_2_5"}


# --- training --------------------------------------------------------------


def _seed_league(db, league: str, seasons: int = 4) -> None:
    teams = [Team(name=f"{league} {n}", league=league, aliases=[]) for n in ("A", "B", "C", "D", "E", "F")]
    db.add_all(teams)
    db.commit()
    for t in teams:
        db.refresh(t)

    day = 0
    for s in range(seasons):
        for i, home in enumerate(teams):
            for j, away in enumerate(teams):
                if i == j:
                    continue
                db.add(
                    Match(
                        league=league,
                        season=f"{2020 + s}-{21 + s}",
                        date=dt.datetime(2020, 8, 1, 15, 0) + dt.timedelta(days=day),
                        home_team_id=home.id,
                        away_team_id=away.id,
                        # Stronger teams earlier in the list, with some noise,
                        # so every outcome class appears.
                        home_score=(3 + j - i + day) % 4,
                        away_score=(1 + i + day) % 3,
                        status="FINISHED",
                    )
                )
                day += 3
    db.commit()


def test_one_backtest_fits_one_set_of_weights_on_every_leagues_validation(db_session, model_dir, monkeypatch):
    backtest = _load_backtest()
    leagues = ["Blend League One", "Blend League Two"]
    for league in leagues:
        _seed_league(db_session, league)

    seen: dict[str, int] = {}

    def fake_fit(breakdowns, labels, step=0.05):
        seen["n"] = len(breakdowns)
        assert len(breakdowns) == len(labels)
        return EnsembleWeights(elo=0.45, poisson=0.35, ml=0.2)

    monkeypatch.setattr(backtest, "fit_ensemble_weights", fake_fit)

    # A stale per-league 1X2 calibrator from an older run, which must go.
    stale = MarketCalibrator()
    stale.save(calibrator_path(leagues[0], "1x2"))

    # The cup competition is in KNOWN_LEAGUES and has no Elo replay of its
    # own; it must be skipped, not crash the run.
    backtest.run_all_leagues(db_session, [*leagues, "UEFA Champions League"], False, 0.70, 0.15)

    per_league_val = []
    for league in leagues:
        matches = backtest._finished_matches(db_session, league)
        train_end, val_end = backtest.compute_split(matches, 0.70, 0.15)
        per_league_val.append(sum(1 for m in matches if train_end <= m.date < val_end))

    assert seen["n"] == sum(per_league_val), "weights must be fitted on every league's validation matches pooled"
    assert load_ensemble_weights(leagues[0]) == EnsembleWeights(elo=0.45, poisson=0.35, ml=0.2)
    for league in leagues:
        assert ml_model_path(league).exists(), "each league keeps its own ML model"
        assert not calibrator_path(league, "1x2").exists()
        assert calibrator_path(league, "over_2_5").exists()
    assert not ml_model_path(GLOBAL_MODEL_KEY).exists()
