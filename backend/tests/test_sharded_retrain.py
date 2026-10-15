"""The sharded retrain path (scripts/backtest.py --rebuild-elo /
--train-shard / --combine-weights / --eval-shard, driven by
.github/workflows/refresh-predictions.yml's matrix jobs) must reach the
same end state a single in-process --all-leagues run does: one set of
blend weights fitted on every league's validation matches pooled, not just
whichever shard happened to run, and every league still gets its own
trained model, its own calibrators, and its own stored test metrics.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
from pathlib import Path

import pytest

from app import model_store
from app.db.models import Match, ModelMetric, Team
from app.model_store import calibrator_path, load_ensemble_weights, ml_model_path
from app.prediction_models.ensemble import EnsembleWeights

LEAGUES = ["Shard League One", "Shard League Two", "Shard League Three"]


@pytest.fixture()
def model_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(model_store, "MODEL_DIR", tmp_path)
    return tmp_path


def _load_backtest():
    path = Path(__file__).resolve().parents[1] / "scripts" / "backtest.py"
    spec = importlib.util.spec_from_file_location("backtest_under_test_sharded", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
                        home_score=(3 + j - i + day) % 4,
                        away_score=(1 + i + day) % 3,
                        status="FINISHED",
                    )
                )
                day += 3
    db.commit()


def test_sharded_retrain_matches_a_single_process_run(db_session, model_dir, tmp_path):
    backtest = _load_backtest()
    for league in LEAGUES:
        _seed_league(db_session, league)

    pooled_calls: list[int] = []
    real_fit = backtest.fit_ensemble_weights

    def spying_fit(breakdowns, labels, step=0.05):
        pooled_calls.append(len(breakdowns))
        return real_fit(breakdowns, labels, step)

    backtest.rebuild_elo_for_leagues(db_session, LEAGUES)

    shard_a, shard_b = LEAGUES[:1], LEAGUES[1:]
    val_a = tmp_path / "shard_a.json"
    val_b = tmp_path / "shard_b.json"
    backtest.train_shard(db_session, shard_a, str(val_a), 0.70, 0.15)
    backtest.train_shard(db_session, shard_b, str(val_b), 0.70, 0.15)

    # Every shard's models exist before any shard is scored -- scoring one
    # shard must never have silently retrained another's.
    for league in LEAGUES:
        assert ml_model_path(league).exists()

    backtest.fit_ensemble_weights = spying_fit
    try:
        backtest.combine_weights_cli([str(val_a), str(val_b)])
    finally:
        backtest.fit_ensemble_weights = real_fit

    # Reproduce the exact validation-match count a single --all-leagues run
    # would have pooled, independently of the sharded path.
    all_breakdowns = 0
    for league in LEAGUES:
        matches = backtest._finished_matches(db_session, league)
        train_end, val_end = backtest.compute_split(matches, 0.70, 0.15)
        all_breakdowns += sum(1 for m in matches if train_end <= m.date < val_end)
    assert pooled_calls == [all_breakdowns], "the combine step must fit on every shard's validation matches pooled, not just one"

    fitted_weights = load_ensemble_weights(LEAGUES[0])
    for league in LEAGUES:
        assert load_ensemble_weights(league) == fitted_weights, "every league must read the one shared fit"

    backtest.eval_shard(db_session, shard_a, str(val_a), 0.70, 0.15)
    backtest.eval_shard(db_session, shard_b, str(val_b), 0.70, 0.15)

    for league in LEAGUES:
        assert calibrator_path(league, "over_2_5").exists()
        assert calibrator_path(league, "btts").exists()
        assert not calibrator_path(league, "1x2").exists()
        n = (
            db_session.query(ModelMetric)
            .filter(
                ModelMetric.league == league,
                ModelMetric.metric_name == "accuracy",
                ModelMetric.split == "test",
                ModelMetric.model_version == backtest.MODEL_VERSION,
            )
            .count()
        )
        assert n == 1, f"{league} should have exactly one stored test accuracy row for the real model (baselines get their own model_version)"


def test_eval_shard_skips_a_league_whose_shard_file_doesnt_cover_it(db_session, model_dir, tmp_path):
    """A shard's --eval-shard call is only ever given its own --train-shard
    output -- if a league somehow isn't in that file, this must skip it
    (print and move on), not crash a run that's otherwise fine."""

    backtest = _load_backtest()
    _seed_league(db_session, LEAGUES[0])
    backtest.rebuild_elo_for_leagues(db_session, [LEAGUES[0]])

    empty_file = tmp_path / "empty.json"
    empty_file.write_text(json.dumps({}))

    backtest.eval_shard(db_session, [LEAGUES[0]], str(empty_file), 0.70, 0.15)

    assert not calibrator_path(LEAGUES[0], "over_2_5").exists()


def test_combine_weights_cli_needs_no_database(tmp_path, model_dir):
    """The combine step runs in its own job with no DATABASE_URL secret
    needed -- fit_ensemble_weights and save_ensemble_weights are pure
    file/array work on what --train-shard already wrote."""

    backtest = _load_backtest()
    breakdown = {
        "elo": {"home_win": 0.5, "draw": 0.25, "away_win": 0.25},
        "poisson": {"home_win": 0.4, "draw": 0.3, "away_win": 0.3},
        "ml": {"H": 0.45, "D": 0.25, "A": 0.3},
    }
    data = {
        "Some League": {
            "labels": ["H", "D", "A", "H"],
            "breakdowns": [breakdown, breakdown, breakdown, breakdown],
            "over25_probs": [0.5, 0.5, 0.5, 0.5],
            "over25_actual": [1, 0, 1, 0],
            "btts_probs": [0.5, 0.5, 0.5, 0.5],
            "btts_actual": [1, 0, 1, 0],
        }
    }
    path = tmp_path / "shard.json"
    path.write_text(json.dumps(data))

    backtest.combine_weights_cli([str(path)])

    weights = load_ensemble_weights("Some League")
    assert isinstance(weights, EnsembleWeights)
