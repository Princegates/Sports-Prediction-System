"""Tests for the fitted-ensemble-weights path (app.prediction_models.ensemble).

The bug this guards against: model_breakdown stores each component's
probabilities under display-friendly keys (home_win/draw/away_win), while
blend_1x2 operates on the short H/D/A keys used internally. Mixing the two up
raises a KeyError the moment a real (non-trivial) breakdown is fitted against
-- which only shows up once scripts/backtest.py runs for real, not in an
import-only smoke test.
"""

from __future__ import annotations

from app.prediction_models.ensemble import EnsembleWeights, blend_1x2, breakdown_to_hda, fit_ensemble_weights


def _breakdown(home_win: float, draw: float, away_win: float) -> dict[str, float]:
    return {"home_win": home_win, "draw": draw, "away_win": away_win}


def test_breakdown_to_hda_converts_display_keys_to_short_labels():
    assert breakdown_to_hda(_breakdown(0.5, 0.3, 0.2)) == {"H": 0.5, "D": 0.3, "A": 0.2}


def test_blend_1x2_weights_components_correctly():
    elo = {"H": 1.0, "D": 0.0, "A": 0.0}
    poisson = {"H": 0.0, "D": 1.0, "A": 0.0}
    weights = EnsembleWeights(elo=0.5, poisson=0.5, ml=0.0)

    blended = blend_1x2(elo, poisson, None, weights)

    assert blended["H"] == blended["D"] == 0.5
    assert blended["A"] == 0.0


def test_fit_ensemble_weights_runs_on_real_shaped_breakdowns_without_crashing():
    """The exact shape scripts/backtest.py passes: model_breakdown dicts with
    display keys, an 'ml' component sometimes absent (None)."""

    breakdowns = [
        {"elo": _breakdown(0.5, 0.3, 0.2), "poisson": _breakdown(0.4, 0.3, 0.3), "ml": _breakdown(0.6, 0.2, 0.2)},
        {"elo": _breakdown(0.2, 0.3, 0.5), "poisson": _breakdown(0.3, 0.3, 0.4), "ml": None},
        {"elo": _breakdown(0.6, 0.2, 0.2), "poisson": _breakdown(0.5, 0.2, 0.3), "ml": _breakdown(0.55, 0.25, 0.2)},
    ]
    actual = ["H", "A", "H"]

    weights = fit_ensemble_weights(breakdowns, actual, step=0.5)

    assert abs(weights.elo + weights.poisson + weights.ml - 1.0) < 1e-6
    assert 0.0 <= weights.elo <= 1.0
    assert 0.0 <= weights.poisson <= 1.0
    assert 0.0 <= weights.ml <= 1.0


def test_fit_ensemble_weights_prefers_the_component_that_matches_reality():
    """When one component is always right and the others are always wrong,
    the fit should push weight toward the accurate one."""

    always_right = _breakdown(0.9, 0.05, 0.05)
    always_wrong = _breakdown(0.05, 0.05, 0.9)

    breakdowns = [{"elo": always_right, "poisson": always_wrong, "ml": always_wrong}] * 20
    actual = ["H"] * 20

    weights = fit_ensemble_weights(breakdowns, actual, step=0.1)

    assert weights.elo > weights.poisson
    assert weights.elo > weights.ml
