"""Which trained model actually serves a league's predictions.

This order was inverted once, and nothing caught it. `--pool-leagues` writes
ml_model_global.joblib and no per-league files; load_ml_model() preferred the
global one; and bootstrap.py ran `--pool-leagues` for any multi-league
invocation -- which the refresh workflow always is. So the weekly retrain
redeployed the pooled model every week, while ROADMAP.md recorded the
measured decision to use per-league models and a local file deletion that
production regenerated seven days later.

Nothing failed. The predictions were simply the worse ones (49.7% mean
against 50.1%, -3.8 on the Premier League), which is invisible without a
backtest. Hence a test on the selection itself.
"""

from __future__ import annotations

import pytest

from app import model_store
from app.model_store import GLOBAL_MODEL_KEY, load_ml_model, ml_model_path
from app.prediction_models.ml_model import MLModel

LEAGUE = "English Premier League"


@pytest.fixture()
def model_dir(tmp_path, monkeypatch):
    """Redirects model_artifacts at the module the path helper reads, so a
    test never writes into the real one."""

    monkeypatch.setattr(model_store, "MODEL_DIR", tmp_path)
    return tmp_path


def _write_model(path, tag: str) -> None:
    """A loadable artifact carrying a marker, so the assertions can say which
    file was chosen rather than merely that something loaded."""

    model = MLModel()
    model._result_classes = [tag]
    model.save(path)


def test_prefers_the_leagues_own_model_over_the_pooled_one(model_dir):
    _write_model(ml_model_path(LEAGUE), "per-league")
    _write_model(ml_model_path(GLOBAL_MODEL_KEY), "pooled")

    loaded = load_ml_model(LEAGUE)

    assert loaded is not None
    assert loaded._result_classes == ["per-league"], (
        "the pooled model measured worse; it must not override a league that has its own"
    )


def test_falls_back_to_the_pooled_model_when_the_league_has_none(model_dir):
    """A league too new or small to train on is what pooling is for."""

    _write_model(ml_model_path(GLOBAL_MODEL_KEY), "pooled")

    loaded = load_ml_model("Some Newly Added League")

    assert loaded is not None
    assert loaded._result_classes == ["pooled"]


def test_returns_none_when_nothing_is_trained(model_dir):
    """The ensemble drops the ML component and blends Elo/Poisson instead --
    it must not raise."""

    assert load_ml_model(LEAGUE) is None
