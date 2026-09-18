"""Ensemble engine (spec section 24): blends Elo, Poisson and Gradient
Boosting into one set of calibrated market probabilities, and reports how
much the underlying models agree (spec section 34).
"""

from __future__ import annotations

import datetime as dt
import statistics
from dataclasses import dataclass, field

import numpy as np
from sqlalchemy.orm import Session

from app.config import get_settings
from app.prediction_models import elo, poisson_model
from app.prediction_models.calibration import MarketCalibrator
from app.prediction_models.ml_model import LeagueFeatureCache, MLModel, build_feature_row

MAX_STD_FOR_THREE_PROBS = 0.471  # std of [1, 0, 0] -- theoretical max disagreement


@dataclass
class EnsembleResult:
    home_win: float
    draw: float
    away_win: float
    over_probabilities: dict[str, float]
    btts_yes: float
    btts_no: float
    correct_score_probabilities: dict[str, float]
    most_likely_score: str
    most_likely_score_probability: float
    model_agreement_1x2: float
    model_breakdown: dict = field(default_factory=dict)


def _renormalize(probs: dict[str, float]) -> dict[str, float]:
    total = sum(probs.values())
    if total <= 0:
        n = len(probs)
        return {k: 1.0 / n for k in probs}
    return {k: v / total for k, v in probs.items()}


def _weighted_blend(components: list[tuple[dict[str, float], float]]) -> dict[str, float]:
    keys = components[0][0].keys()
    total_weight = sum(w for _, w in components)
    if total_weight <= 0:
        # Every component present for this match got zero weight -- e.g. the
        # ensemble-weight grid search tried "all weight on the ML model" for
        # a match where the ML model had no prediction. Falling back to an
        # equal blend among what IS available keeps the match scorable
        # instead of raising, rather than silently favoring whichever
        # component happens to iterate first.
        n = len(components)
        blended = {k: sum(probs[k] for probs, _ in components) / n for k in keys}
        return _renormalize(blended)
    blended = {k: sum(probs[k] * w for probs, w in components) / total_weight for k in keys}
    return _renormalize(blended)


def blend_1x2(
    elo_probs: dict[str, float],
    poisson_probs: dict[str, float],
    ml_probs: dict[str, float] | None,
    weights: "EnsembleWeights",
) -> dict[str, float]:
    """The same blend ``generate_prediction`` does internally, exposed so a
    calibrator can be refit against a different weight choice without
    recomputing Elo/Poisson/the GBM from scratch -- used by
    ``scripts/backtest.py`` when fitting per-league weights.

    Expects H/D/A-keyed dicts -- the same shape ``elo_probs``/``poisson_probs``/
    ``ml_probs`` have inside this module. ``model_breakdown`` stores the same
    numbers under display-friendly keys (``home_win``/``draw``/``away_win``)
    instead; convert with ``breakdown_to_hda`` first if that's what you have.
    """

    components = [(elo_probs, weights.elo), (poisson_probs, weights.poisson)]
    if ml_probs is not None:
        components.append((ml_probs, weights.ml))
    return _weighted_blend(components)


def breakdown_to_hda(component: dict[str, float]) -> dict[str, float]:
    """Converts one ``model_breakdown["elo"|"poisson"]`` entry (keyed
    ``home_win``/``draw``/``away_win``, plus whatever extra diagnostic fields
    that component carries) to the H/D/A keys ``blend_1x2`` expects.

    **Not for the ``"ml"`` entry.** ``model_breakdown`` is not uniform: the
    elo and poisson entries carry display keys, but ``"ml"`` stores the raw
    H/D/A probabilities the model produced (see ``generate_prediction``
    below), and downstream consumers read it that way -- the assistant's
    responder does ``ml.get("H")``. Passing it here raises ``KeyError:
    'home_win'``, which is exactly how this was found: every real backtest
    crashed the moment a trained ML model existed, while the unit tests
    passed because they hand-built the "ml" entry in the wrong shape.
    """

    return {"H": component["home_win"], "D": component["draw"], "A": component["away_win"]}


@dataclass
class EnsembleWeights:
    elo: float
    poisson: float
    ml: float

    @classmethod
    def from_settings(cls) -> "EnsembleWeights":
        settings = get_settings()
        return cls(elo=settings.ensemble_weight_elo, poisson=settings.ensemble_weight_poisson, ml=settings.ensemble_weight_ml)


def fit_ensemble_weights(
    breakdowns: list[dict], actual: list[str], step: float = 0.05
) -> EnsembleWeights:
    """Grid-searches (w_elo, w_poisson, w_ml), summing to 1, for the
    combination that minimizes log loss on already-computed validation
    predictions -- replacing the hand-set 0.30/0.35/0.35 default with
    whatever the validation data actually supports for this league.

    ``breakdowns`` is a list of ``{"elo": {...}, "poisson": {...}, "ml":
    {...} | None}`` dicts -- exactly ``EnsembleResult.model_breakdown`` --
    one per validation match, gathered without needing to know the weights
    in advance since the three component models don't depend on them.
    """

    from sklearn.metrics import log_loss

    labels_sorted = sorted({"H", "D", "A"})
    steps = np.arange(0.0, 1.0 + 1e-9, step)

    # Converted once here rather than inside the grid search below -- the
    # conversion doesn't depend on the weights being tried, so redoing it on
    # every one of the ~400 grid points would be pure waste.
    #
    # Note "ml" is NOT converted: unlike elo/poisson (stored under display
    # keys home_win/draw/away_win), model_breakdown["ml"] is ml_probs
    # verbatim, already H/D/A-keyed -- the same asymmetry the frontend's
    # ModelBreakdown type already encodes (elo/poisson get the long keys,
    # ml doesn't). Converting it here would look for a "home_win" key it
    # doesn't have.
    converted = [
        (breakdown_to_hda(b["elo"]), breakdown_to_hda(b["poisson"]), b.get("ml"))
        for b in breakdowns
    ]

    best_weights = EnsembleWeights.from_settings()
    best_ll = float("inf")

    for w_elo in steps:
        for w_poisson in steps:
            w_ml = 1.0 - w_elo - w_poisson
            if w_ml < -1e-9 or w_ml > 1.0 + 1e-9:
                continue
            w_ml = max(0.0, w_ml)
            weights = EnsembleWeights(elo=float(w_elo), poisson=float(w_poisson), ml=float(w_ml))

            probs_matrix = []
            for elo_hda, poisson_hda, ml_hda in converted:
                blended = blend_1x2(elo_hda, poisson_hda, ml_hda, weights)
                probs_matrix.append([blended[label] for label in labels_sorted])

            try:
                ll = log_loss(actual, probs_matrix, labels=labels_sorted)
            except ValueError:
                continue
            if ll < best_ll:
                best_ll = ll
                best_weights = weights

    return best_weights


def _agreement(values: list[float]) -> float:
    if len(values) < 2:
        return 1.0
    std = statistics.pstdev(values)
    return max(0.0, min(1.0, 1 - std / MAX_STD_FOR_THREE_PROBS))


def generate_prediction(
    db: Session,
    home_team_id: int,
    away_team_id: int,
    league: str,
    as_of: dt.datetime,
    ml_model: MLModel | None = None,
    calibrators: dict[str, MarketCalibrator] | None = None,
    weights: "EnsembleWeights | None" = None,
    feature_cache: LeagueFeatureCache | None = None,
) -> EnsembleResult:
    """``feature_cache`` is an optimization for callers predicting many
    matches in one league: without it every call reloads that league's whole
    history to build one feature row."""

    settings = get_settings()
    calibrators = calibrators or {}
    weights = weights or EnsembleWeights.from_settings()

    # --- Model 1: Elo ---------------------------------------------------
    home_elo = elo.get_rating_before(db, home_team_id, as_of, settings.elo_start_rating)
    away_elo = elo.get_rating_before(db, away_team_id, as_of, settings.elo_start_rating)
    elo_diff = (home_elo + settings.home_advantage_elo) - away_elo
    elo_1x2 = elo.elo_diff_to_1x2(elo_diff)
    elo_probs = {"H": elo_1x2.home_win, "D": elo_1x2.draw, "A": elo_1x2.away_win}

    # --- Models 2/3: Poisson ---------------------------------------------
    goal_markets = poisson_model.predict(db, home_team_id, away_team_id, league, as_of)
    poisson_probs = {"H": goal_markets.home_win, "D": goal_markets.draw, "A": goal_markets.away_win}

    # --- Model 4: Gradient boosting (optional -- needs a trained model) --
    ml_probs: dict[str, float] | None = None
    ml_over25 = ml_btts = None
    if ml_model is not None:
        try:
            feature_row = build_feature_row(
                db, home_team_id, away_team_id, league, as_of, settings.home_advantage_elo, cache=feature_cache
            )
            ml_pred = ml_model.predict(feature_row)
            ml_probs = {"H": ml_pred.home_win, "D": ml_pred.draw, "A": ml_pred.away_win}
            ml_over25 = ml_pred.over_2_5
            ml_btts = ml_pred.btts_yes
        except RuntimeError:
            ml_probs = None

    blended_1x2 = blend_1x2(elo_probs, poisson_probs, ml_probs, weights)

    if "1x2" in calibrators:
        blended_1x2 = calibrators["1x2"].calibrate(blended_1x2)

    # Goal markets: Poisson is the only model producing full goal-line and
    # correct-score distributions; Over 2.5 / BTTS additionally blend in the
    # ML model's opinion when available (60/40 poisson/ml split).
    over_probs = dict(goal_markets.over_probabilities)
    btts_yes = goal_markets.btts_yes
    if ml_over25 is not None:
        over_probs["2.5"] = 0.6 * goal_markets.over_probabilities["2.5"] + 0.4 * ml_over25
    if ml_btts is not None:
        btts_yes = 0.6 * goal_markets.btts_yes + 0.4 * ml_btts

    if "over_2_5" in calibrators:
        cal = calibrators["over_2_5"].calibrate({"yes": over_probs["2.5"], "no": 1 - over_probs["2.5"]})
        over_probs["2.5"] = cal["yes"]
    if "btts" in calibrators:
        cal = calibrators["btts"].calibrate({"yes": btts_yes, "no": 1 - btts_yes})
        btts_yes = cal["yes"]

    agreement_h = _agreement([elo_probs["H"], poisson_probs["H"]] + ([ml_probs["H"]] if ml_probs else []))
    agreement_d = _agreement([elo_probs["D"], poisson_probs["D"]] + ([ml_probs["D"]] if ml_probs else []))
    agreement_a = _agreement([elo_probs["A"], poisson_probs["A"]] + ([ml_probs["A"]] if ml_probs else []))
    model_agreement = (agreement_h + agreement_d + agreement_a) / 3

    return EnsembleResult(
        home_win=blended_1x2["H"],
        draw=blended_1x2["D"],
        away_win=blended_1x2["A"],
        over_probabilities=over_probs,
        btts_yes=btts_yes,
        btts_no=1 - btts_yes,
        correct_score_probabilities=goal_markets.correct_score_probabilities,
        most_likely_score=goal_markets.most_likely_score,
        most_likely_score_probability=goal_markets.most_likely_score_probability,
        model_agreement_1x2=model_agreement,
        model_breakdown={
            "elo": {"home_win": elo_probs["H"], "draw": elo_probs["D"], "away_win": elo_probs["A"], "elo_diff": elo_diff},
            "poisson": {
                "home_win": poisson_probs["H"],
                "draw": poisson_probs["D"],
                "away_win": poisson_probs["A"],
                "lambda_home": goal_markets.lambda_home,
                "lambda_away": goal_markets.lambda_away,
            },
            "ml": ml_probs,
        },
    )
