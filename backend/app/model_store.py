"""Where trained model artifacts live on disk, and how to load them.

Kept deliberately dumb (local filesystem, joblib) -- there's no paid model
registry involved. ``scripts/backtest.py`` trains and writes these; the API
and ``scripts/build_predictions.py`` read them back.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.prediction_models.calibration import MarketCalibrator
from app.prediction_models.ensemble import EnsembleWeights
from app.prediction_models.ml_model import MLModel

MODEL_DIR = Path(__file__).resolve().parent.parent / "model_artifacts"
MODEL_DIR.mkdir(exist_ok=True)

CALIBRATION_MARKETS = ["1x2", "over_2_5", "btts"]

# Key under which the cross-league model is stored -- see
# app.prediction_models.ml_model.build_pooled_training_dataset. Calibration
# stays per-league (base rates genuinely differ by league), only the
# GradientBoostingClassifier itself is shared.
GLOBAL_MODEL_KEY = "global"


def _slug(league: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", league.lower()).strip("_")


def ml_model_path(league: str) -> Path:
    return MODEL_DIR / f"ml_model_{_slug(league)}.joblib"


def calibrator_path(league: str, market: str) -> Path:
    return MODEL_DIR / f"calibrator_{_slug(league)}_{market}.joblib"


def ensemble_weights_path(league: str) -> Path:
    return MODEL_DIR / f"ensemble_weights_{_slug(league)}.json"


def save_ensemble_weights(league: str, weights: EnsembleWeights) -> None:
    ensemble_weights_path(league).write_text(json.dumps({"elo": weights.elo, "poisson": weights.poisson, "ml": weights.ml}))


def load_ensemble_weights(league: str) -> EnsembleWeights:
    """Falls back to the fixed settings.ensemble_weight_* default for a
    league that hasn't had weights fitted yet (``scripts/backtest.py`` is
    what fits and saves them)."""

    path = ensemble_weights_path(league)
    if not path.exists():
        return EnsembleWeights.from_settings()
    data = json.loads(path.read_text())
    return EnsembleWeights(elo=data["elo"], poisson=data["poisson"], ml=data["ml"])


def load_ml_model(league: str) -> MLModel | None:
    """Prefers the pooled cross-league model when one has been trained
    (``scripts/backtest.py --pool-leagues``); falls back to a per-league
    model for a league that predates that, or if the global file is ever
    removed. This is what lets the global model roll out just by being
    saved, with no call-site changes."""

    global_path = ml_model_path(GLOBAL_MODEL_KEY)
    if global_path.exists():
        return MLModel.load(global_path)

    path = ml_model_path(league)
    if not path.exists():
        return None
    return MLModel.load(path)


def load_calibrators(league: str) -> dict[str, MarketCalibrator]:
    calibrators: dict[str, MarketCalibrator] = {}
    for market in CALIBRATION_MARKETS:
        path = calibrator_path(league, market)
        if path.exists():
            calibrators[market] = MarketCalibrator.load(path)
    return calibrators
