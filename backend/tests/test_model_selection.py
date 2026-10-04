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
from app.prediction_models.ml_model import FEATURE_COLUMNS, MLModel

LEAGUE = "English Premier League"


@pytest.fixture()
def model_dir(tmp_path, monkeypatch):
    """Redirects model_artifacts at the module the path helper reads, so a
    test never writes into the real one."""

    monkeypatch.setattr(model_store, "MODEL_DIR", tmp_path)
    return tmp_path


def _write_model(path, tag: str, *, feature_columns: list[str] | None = None) -> None:
    """A loadable artifact carrying a marker, so the assertions can say which
    file was chosen rather than merely that something loaded. Defaults to
    today's FEATURE_COLUMNS -- the normal, freshly-trained case -- so a test
    only sees the staleness check if it explicitly asks to."""

    model = MLModel()
    model._result_classes = [tag]
    model.feature_columns = list(FEATURE_COLUMNS) if feature_columns is None else feature_columns
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


# --- staleness: a cached file fit on a different feature schema -----------
#
# The incident this guards against: KNOWN_LEAGUES grew (adding UEFA
# Champions League itself as a one-hot column), widening FEATURE_COLUMNS.
# Only the pooled/global file gets rewritten by the normal multi-league
# training path, so a league's own older file silently keeps the narrower
# shape forever -- and without this check, load_ml_model hands it back as
# if it were fine, and GradientBoostingClassifier.predict_proba raises deep
# inside the ensemble on the shape mismatch.


def test_falls_back_to_the_pooled_model_when_the_leagues_own_file_is_stale(model_dir):
    _write_model(ml_model_path(LEAGUE), "stale-per-league", feature_columns=FEATURE_COLUMNS[:-1])
    _write_model(ml_model_path(GLOBAL_MODEL_KEY), "pooled")

    loaded = load_ml_model(LEAGUE)

    assert loaded is not None
    assert loaded._result_classes == ["pooled"]


def test_returns_none_when_every_available_file_is_stale(model_dir):
    """Not even the pooled fallback is usable -- same as no model trained at
    all, never a crash."""

    _write_model(ml_model_path(LEAGUE), "stale-per-league", feature_columns=FEATURE_COLUMNS[:-1])
    _write_model(ml_model_path(GLOBAL_MODEL_KEY), "stale-pooled", feature_columns=[])

    assert load_ml_model(LEAGUE) is None


def test_an_old_format_file_with_no_feature_columns_reads_as_stale(model_dir):
    """A file saved before feature_columns existed at all has nothing stored
    under that key -- MLModel.load defaults it to [], which can never equal
    today's non-empty FEATURE_COLUMNS, so it's treated as incompatible
    rather than assumed fine by omission."""

    path = ml_model_path(LEAGUE)
    model = MLModel()
    model._result_classes = ["old-format"]
    import joblib
    joblib.dump(
        {"result_clf": None, "over25_clf": None, "btts_clf": None, "result_classes": ["old-format"]}, path,
    )
    _write_model(ml_model_path(GLOBAL_MODEL_KEY), "pooled")

    loaded = load_ml_model(LEAGUE)

    assert loaded is not None
    assert loaded._result_classes == ["pooled"]
