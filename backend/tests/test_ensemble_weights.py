"""Tests for the fitted-ensemble-weights path (app.prediction_models.ensemble).

model_breakdown is asymmetric by design, matching frontend/src/types.ts's
ModelBreakdown: elo/poisson are stored under display-friendly keys
(home_win/draw/away_win, plus extra diagnostic fields), while ml is
ml_probs verbatim -- already H/D/A-keyed. blend_1x2 needs H/D/A throughout,
so elo/poisson must be converted with breakdown_to_hda and ml must NOT be
(it's already the right shape). Both directions of getting this wrong raise
a KeyError that only shows up once scripts/backtest.py runs against a real
database, not in an import-only smoke test -- these tests fit real-shaped
breakdowns to catch it here instead.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.db.models import Match, Team
from app.prediction_models import elo
from app.prediction_models.ensemble import (
    EnsembleWeights,
    blend_1x2,
    breakdown_to_hda,
    fit_ensemble_weights,
    generate_prediction,
)
from app.prediction_models.ml_model import MLPrediction

LEAGUE = "Weights Test League"
BASE = dt.datetime(2025, 1, 1, 15, 0)


def _display_keyed(home_win: float, draw: float, away_win: float) -> dict[str, float]:
    """Shape of model_breakdown["elo"] / ["poisson"]."""
    return {"home_win": home_win, "draw": draw, "away_win": away_win}


def _hda_keyed(home_win: float, draw: float, away_win: float) -> dict[str, float]:
    """Shape of model_breakdown["ml"] -- ml_probs verbatim."""
    return {"H": home_win, "D": draw, "A": away_win}


def test_breakdown_to_hda_converts_display_keys_to_short_labels():
    assert breakdown_to_hda(_display_keyed(0.5, 0.3, 0.2)) == {"H": 0.5, "D": 0.3, "A": 0.2}


def test_blend_1x2_weights_components_correctly():
    elo = {"H": 1.0, "D": 0.0, "A": 0.0}
    poisson = {"H": 0.0, "D": 1.0, "A": 0.0}
    weights = EnsembleWeights(elo=0.5, poisson=0.5, ml=0.0)

    blended = blend_1x2(elo, poisson, None, weights)

    assert blended["H"] == blended["D"] == 0.5
    assert blended["A"] == 0.0


def test_fit_ensemble_weights_runs_on_real_shaped_breakdowns_without_crashing():
    """The exact shape scripts/backtest.py passes: elo/poisson under display
    keys, ml already H/D/A-keyed (or absent) -- the same asymmetry
    generate_prediction's model_breakdown always has."""

    breakdowns = [
        {"elo": _display_keyed(0.5, 0.3, 0.2), "poisson": _display_keyed(0.4, 0.3, 0.3), "ml": _hda_keyed(0.6, 0.2, 0.2)},
        {"elo": _display_keyed(0.2, 0.3, 0.5), "poisson": _display_keyed(0.3, 0.3, 0.4), "ml": None},
        {"elo": _display_keyed(0.6, 0.2, 0.2), "poisson": _display_keyed(0.5, 0.2, 0.3), "ml": _hda_keyed(0.55, 0.25, 0.2)},
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

    always_right = _display_keyed(0.9, 0.05, 0.05)
    always_wrong_poisson = _display_keyed(0.05, 0.05, 0.9)
    always_wrong_ml = _hda_keyed(0.05, 0.05, 0.9)

    breakdowns = [{"elo": always_right, "poisson": always_wrong_poisson, "ml": always_wrong_ml}] * 20
    actual = ["H"] * 20

    weights = fit_ensemble_weights(breakdowns, actual, step=0.1)

    assert weights.elo > weights.poisson
    assert weights.elo > weights.ml


def test_breakdown_to_hda_rejects_an_already_converted_entry():
    """Guards the direction of the conversion. Silently passing an H/D/A dict
    through would let the ml/elo mix-up return plausible nonsense instead of
    failing loudly."""

    with pytest.raises(KeyError):
        breakdown_to_hda(_hda_keyed(0.5, 0.3, 0.2))


class _StubMLModel:
    """Stands in for a trained GradientBoostingClassifier.

    The ``ml`` entry is only populated when a model is passed, so without
    this the test below would exercise everything except the line that
    actually broke. Fitting a real one would need a training set and would
    be testing sklearn, not this.
    """

    def predict(self, feature_row: dict[str, float]) -> MLPrediction:
        return MLPrediction(home_win=0.5, draw=0.3, away_win=0.2, over_2_5=0.55, btts_yes=0.5)


def test_fit_ensemble_weights_accepts_a_real_generated_breakdown(db_session):
    """No literal breakdowns: this one comes from generate_prediction.

    The tests above assert the asymmetry as their authors understand it,
    which is exactly how the original version of this file went wrong -- it
    hand-built the "ml" entry in the elo/poisson shape and passed for weeks
    while every real backtest crashed. Reading the shape off real output
    instead is what makes it a regression test rather than a restatement.
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

    assert set(result.model_breakdown["elo"]) >= {"home_win", "draw", "away_win"}
    assert set(result.model_breakdown["ml"]) == {"H", "D", "A"}

    weights = fit_ensemble_weights([result.model_breakdown], ["H"], step=0.5)
    assert abs(weights.elo + weights.poisson + weights.ml - 1.0) < 1e-6


def test_generate_prediction_persists_half_time_lambdas(db_session):
    """model_breakdown["poisson"] must carry lambda_home_ht/lambda_away_ht
    alongside the existing full-time ones -- app.outcomes.registry rebuilds
    every HT market from exactly these two numbers at read time, so if this
    silently stopped being populated, HT markets would silently vanish
    everywhere without a single test failing there."""

    home = Team(name="Gamma FC", league=LEAGUE, aliases=[])
    away = Team(name="Delta FC", league=LEAGUE, aliases=[])
    db_session.add_all([home, away])
    db_session.commit()
    db_session.refresh(home)
    db_session.refresh(away)

    for day in range(10):
        db_session.add(
            Match(
                league=LEAGUE, season="2024-25", date=BASE + dt.timedelta(days=day),
                home_team_id=home.id if day % 2 == 0 else away.id,
                away_team_id=away.id if day % 2 == 0 else home.id,
                home_score=2, away_score=1, ht_home_score=1, ht_away_score=0,
                status="FINISHED",
            )
        )
    db_session.commit()

    as_of = BASE + dt.timedelta(days=30)
    result = generate_prediction(db_session, home.id, away.id, LEAGUE, as_of)

    poisson = result.model_breakdown["poisson"]
    assert "lambda_home_ht" in poisson and "lambda_away_ht" in poisson
    # A fraction strictly between 0 and 1 of the full-time lambda -- never
    # the full 90-minute expectation, never zero.
    assert 0 < poisson["lambda_home_ht"] < poisson["lambda_home"]
    assert 0 < poisson["lambda_away_ht"] < poisson["lambda_away"]
