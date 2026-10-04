"""Where trained model artifacts live on disk, and how to load them.

Kept deliberately dumb (local filesystem, joblib) -- there's no paid model
registry involved. ``scripts/backtest.py`` trains and writes these; the API
and ``scripts/build_predictions.py`` read them back.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from sqlalchemy.orm import Session

from app import app_settings
from app.prediction_models.calibration import MarketCalibrator
from app.prediction_models.ensemble import EnsembleWeights
from app.prediction_models.ml_model import FEATURE_COLUMNS, MLModel

logger = logging.getLogger(__name__)

MODEL_DIR = Path(__file__).resolve().parent.parent / "model_artifacts"
MODEL_DIR.mkdir(exist_ok=True)

# No "1x2": a per-league calibrator for the match result is fitted on ~600
# validation matches, and measured on six leagues' held-out test slices it made
# every league's probabilities worse -- it learns that slice's quirks (one
# season's draw rate, say) rather than a lasting bias. The three component
# models are already close to calibrated on their own; blended with shared
# weights they need no correction. Files an older backtest left behind are
# ignored rather than read.
CALIBRATION_MARKETS = ["over_2_5", "btts"]

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


def save_ensemble_weights(weights: EnsembleWeights) -> None:
    ensemble_weights_path(GLOBAL_MODEL_KEY).write_text(
        json.dumps({"elo": weights.elo, "poisson": weights.poisson, "ml": weights.ml})
    )


# The superadmin panel's blend-weight settings, in EnsembleWeights order.
PANEL_WEIGHT_KEYS = ["ensemble_weight_elo", "ensemble_weight_poisson", "ensemble_weight_ml"]


def load_ensemble_weights(league: str, db: Session | None = None) -> EnsembleWeights:
    """The blend weights for ``league`` -- which are the same for every league.

    In order: the settings panel, if a superadmin has saved any of the three
    weights there (needs ``db``); else the weights the last multi-league
    backtest fitted; else the environment default. The panel comes first
    because saving there is a deliberate decision, and resetting the fields
    hands control back to the fitted weights. It used to come nowhere: nothing
    on the prediction path passed a session, so the panel's weight fields
    changed the page and not a single prediction.

    They used to be fitted per league, on that league's ~600 validation
    matches, and the fit chased noise: the Premier League put 0% on Poisson,
    La Liga 85%, and on the held-out test slices those weights did worse than
    one shared set in all six leagues -- even a shared set fitted without
    seeing the league in question. How much to trust Elo against Poisson
    against the GBM is a property of the models, not of the league.

    So ``scripts/backtest.py --all-leagues`` fits one set on every league's
    validation matches pooled, and this returns it, falling back to the
    settings default (itself set from that pooled fit) before one exists.
    Per-league files an older backtest left behind are ignored. ``league`` is
    kept so callers don't change if a league ever has the data to earn its own.
    """

    if db is not None:
        panel = app_settings.overridden_values(db, PANEL_WEIGHT_KEYS)
        if panel:
            # A weight left unsaved beside saved ones takes the environment
            # default -- the value the panel shows for it.
            default = EnsembleWeights.from_settings()
            return EnsembleWeights(
                elo=float(panel.get("ensemble_weight_elo", default.elo)),
                poisson=float(panel.get("ensemble_weight_poisson", default.poisson)),
                ml=float(panel.get("ensemble_weight_ml", default.ml)),
            )

    path = ensemble_weights_path(GLOBAL_MODEL_KEY)
    if not path.exists():
        return EnsembleWeights.from_settings()
    data = json.loads(path.read_text())
    return EnsembleWeights(elo=data["elo"], poisson=data["poisson"], ml=data["ml"])


def _usable(model: MLModel, path: Path) -> bool:
    """False for a cached artifact fit on a different feature schema than
    the code running right now.

    KNOWN_LEAGUES (app.prediction_models.ml_model) grows as leagues are
    added, which widens FEATURE_COLUMNS' one-hot tail -- and the normal
    multi-league training path only ever rewrites the pooled/global file
    (see this function's own docstring on why per-league files exist at
    all), so a per-league file from before that growth sits untouched and
    silently wrong-shaped forever. Without this check it reaches
    GradientBoostingClassifier.predict_proba, which raises a bare
    ValueError on the shape mismatch deep enough that the caller three
    layers up (scripts/generate_predictions.py) has no graceful way to
    catch it per-match -- the whole league's run dies instead of just
    losing its ML component for one league. This is what actually happened
    to UEFA Champions League: its own file predated the league itself
    being added to KNOWN_LEAGUES' one-hot list.
    """

    if model.feature_columns != FEATURE_COLUMNS:
        logger.warning(
            "%s was fit on %d feature(s), current code expects %d -- treating it as absent.",
            path.name, len(model.feature_columns), len(FEATURE_COLUMNS),
        )
        return False
    return True


def load_ml_model(league: str) -> MLModel | None:
    """This league's own model, falling back to the pooled cross-league one
    when it has none.

    The order matters and used to be the other way round. Pooling was
    measured worse than per-league training -- 49.7% mean against 50.1%,
    losing 3.8 points on the Premier League (ROADMAP.md 1d) -- so preferring
    the pooled file meant one ``--pool-leagues`` run silently demoted every
    league to the worse model, with nothing in the API to show it had
    happened.

    Pooling is still the right answer for a league with too little history to
    train on, which is what a fallback is for: it only applies where the
    alternative is no ML component at all.
    """

    path = ml_model_path(league)
    if path.exists():
        model = MLModel.load(path)
        if _usable(model, path):
            return model

    global_path = ml_model_path(GLOBAL_MODEL_KEY)
    if global_path.exists():
        model = MLModel.load(global_path)
        if _usable(model, global_path):
            return model

    return None


def load_calibrators(league: str) -> dict[str, MarketCalibrator]:
    calibrators: dict[str, MarketCalibrator] = {}
    for market in CALIBRATION_MARKETS:
        path = calibrator_path(league, market)
        if path.exists():
            calibrators[market] = MarketCalibrator.load(path)
    return calibrators
