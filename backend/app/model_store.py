"""Where trained model artifacts live on disk, and how to load them.

Kept deliberately dumb (local filesystem, joblib) -- there's no paid model
registry involved. ``scripts/backtest.py`` trains and writes these; the API
and ``scripts/build_predictions.py`` read them back.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.prediction_models.calibration import MarketCalibrator
from app.prediction_models.ml_model import MLModel

MODEL_DIR = Path(__file__).resolve().parent.parent / "model_artifacts"
MODEL_DIR.mkdir(exist_ok=True)

CALIBRATION_MARKETS = ["1x2", "over_2_5", "btts"]


def _slug(league: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", league.lower()).strip("_")


def ml_model_path(league: str) -> Path:
    return MODEL_DIR / f"ml_model_{_slug(league)}.joblib"


def calibrator_path(league: str, market: str) -> Path:
    return MODEL_DIR / f"calibrator_{_slug(league)}_{market}.joblib"


def load_ml_model(league: str) -> MLModel | None:
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
