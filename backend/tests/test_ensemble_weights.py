"""Tests for the fitted-ensemble-weights path (app.prediction_models.ensemble).

``model_breakdown`` is not uniformly keyed, and that is the trap this file
exists for. Its ``elo`` and ``poisson`` entries use display keys
(``home_win``/``draw``/``away_win``); its ``ml`` entry stores the raw H/D/A
probabilities, because that is what the model produces and what downstream
consumers -- the assistant's responder, for one -- read back.

An earlier version of these tests hand-built the ``ml`` entry with display
keys and asserted it was "the exact shape scripts/backtest.py passes". It
wasn't. The suite passed while every real backtest died with ``KeyError:
'home_win'`` the moment a trained ML model existed, so training silently
stopped happening in the nightly job.

Hence ``test_fit_ensemble_weights_accepts_a_real_generated_breakdown``, which
gets its breakdown from ``generate_prediction`` rather than from a literal.
A hand-written fixture can only ever assert what its author already believed.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.db.models import Match, Team
from app.prediction_models import elo
from app.prediction_models.ml_model import MLPrediction
from app.prediction_models.ensemble import (
    EnsembleWeights,
    blend_1x2,
    breakdown_to_hda,
    fit_ensemble_weights,
    generate_prediction,
)

LEAGUE = "Weights Test League"
BASE = dt.datetime(2025, 1, 1, 15, 0)


def _breakdown(home_win: float, draw: float, away_win: float) -> dict[str, float]:
    """An ``elo``/``poisson`` entry: display keys."""

    return {"home_win": home_win, "draw": draw, "away_win": away_win}


def _ml(h: float, d: float, a: float) -> dict[str, float]:
    """An ``ml`` entry: short keys, as ``generate_prediction`` stores it."""

    return {"H": h, "D": d, "A": a}


def test_breakdown_to_hda_converts_display_keys_to_short_labels():
    assert breakdown_to_hda(_breakdown(0.5, 0.3, 0.2)) == {"H": 0.5, "D": 0.3, "A": 0.2}


def test_breakdown_to_hda_rejects_an_already_converted_entry():
    """Guards the direction of the conversion. Silently passing an H/D/A dict
    through would let the ml/elo mix-up return plausible nonsense instead of
    failing loudly."""

    with pytest.raises(KeyError):
        breakdown_to_hda(_ml(0.5, 0.3, 0.2))


def test_blend_1x2_weights_components_correctly():
    elo_probs = {"H": 1.0, "D": 0.0, "A": 0.0}
    poisson_probs = {"H": 0.0, "D": 1.0, "A": 0.0}
    weights = EnsembleWeights(elo=0.5, poisson=0.5, ml=0.0)

    blended = blend_1x2(elo_probs, poisson_probs, None, weights)

    assert blended["H"] == blended["D"] == 0.5
    assert blended["A"] == 0.0


def test_fit_ensemble_weights_runs_on_real_shaped_breakdowns_without_crashing():
    """Mixed shapes, and an 'ml' component that is sometimes absent."""

    breakdowns = [
        {"elo": _breakdown(0.5, 0.3, 0.2), "poisson": _breakdown(0.4, 0.3, 0.3), "ml": _ml(0.6, 0.2, 0.2)},
        {"elo": _breakdown(0.2, 0.3, 0.5), "poisson": _breakdown(0.3, 0.3, 0.4), "ml": None},
        {"elo": _breakdown(0.6, 0.2, 0.2), "poisson": _breakdown(0.5, 0.2, 0.3), "ml": _ml(0.55, 0.25, 0.2)},
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

    breakdowns = [{"elo": always_right, "poisson": always_wrong, "ml": _ml(0.05, 0.05, 0.9)}] * 20
    actual = ["H"] * 20

    weights = fit_ensemble_weights(breakdowns, actual, step=0.1)

    assert weights.elo > weights.poisson
    assert weights.elo > weights.ml


class _StubMLModel:
    """Stands in for a trained GradientBoostingClassifier.

    The ``ml`` entry is only populated when a model is passed, and that entry
    is the one with the divergent key shape -- so without a model here the
    test would exercise everything except the line that actually broke.
    Fitting a real one would need a training set and would test sklearn, not
    this.
    """

    def predict(self, feature_row: dict[str, float]) -> MLPrediction:
        return MLPrediction(home_win=0.5, draw=0.3, away_win=0.2, over_2_5=0.55, btts_yes=0.5)


def test_fit_ensemble_weights_accepts_a_real_generated_breakdown(db_session):
    """The regression test proper: no literal breakdowns anywhere.

    Builds a small league, runs the real ensemble over it, and fits weights
    against what ``generate_prediction`` actually emitted. This is what the
    nightly job does, and it is what a hand-written fixture failed to catch.
    """

    home = Team(name="Alpha FC", league=LEAGUE, aliases=[])
    away = Team(name="Beta FC", league=LEAGUE, aliases=[])
    db_session.add_all([home, away])
    db_session.commit()
    db_session.refresh(home)
    db_session.refresh(away)

    for day in range(20):
        db_session.add(
            Match(
                league=LEAGUE,
                season="2024-25",
                date=BASE + dt.timedelta(days=day),
                home_team_id=home.id if day % 2 == 0 else away.id,
                away_team_id=away.id if day % 2 == 0 else home.id,
                home_score=2,
                away_score=1,
                status="FINISHED",
            )
        )
    db_session.commit()
    elo.rebuild_elo_history(db_session, LEAGUE)

    as_of = BASE + dt.timedelta(days=30)
    result = generate_prediction(db_session, home.id, away.id, LEAGUE, as_of, ml_model=_StubMLModel())

    # The two shapes, asserted from a breakdown nobody hand-wrote. The old
    # fixture had these the same way round, which is what hid the crash.
    assert set(result.model_breakdown["elo"]) >= {"home_win", "draw", "away_win"}
    assert set(result.model_breakdown["ml"]) == {"H", "D", "A"}

    weights = fit_ensemble_weights([result.model_breakdown], ["H"], step=0.5)
    assert abs(weights.elo + weights.poisson + weights.ml - 1.0) < 1e-6
