import numpy as np

from app.prediction_models.calibration import (
    MAX_PROB,
    MIN_PROB,
    MIN_SAMPLES_FOR_ISOTONIC,
    MarketCalibrator,
    PlattCalibrator,
)


def test_calibration_never_outputs_hard_zero_or_one():
    calibrator = MarketCalibrator()
    # Validation data where the rare class ("H") never actually happened in
    # the low-probability bucket -- isotonic regression will legitimately
    # want to map that bucket to 0.0.
    predicted = {
        "H": np.array([0.05, 0.06, 0.07, 0.5, 0.9]),
        "D": np.array([0.2, 0.2, 0.2, 0.2, 0.05]),
        "A": np.array([0.75, 0.74, 0.73, 0.3, 0.05]),
    }
    actual = {
        "H": np.array([0, 0, 0, 1, 1]),
        "D": np.array([0, 0, 0, 0, 0]),
        "A": np.array([1, 1, 1, 0, 0]),
    }
    calibrator.fit(predicted, actual)

    result = calibrator.calibrate({"H": 0.05, "D": 0.2, "A": 0.75})

    assert abs(sum(result.values()) - 1.0) < 1e-9
    for value in result.values():
        assert MIN_PROB * 0.99 <= value <= MAX_PROB  # renormalization can nudge it slightly
        assert value > 0.0


def _synthetic_validation(n: int, seed: int = 7):
    """A validation split whose raw probabilities carry real signal: the
    outcome genuinely occurs more often when the stated probability is
    higher, but with a systematic bias the calibrator should correct."""

    rng = np.random.default_rng(seed)
    raw = rng.uniform(0.15, 0.75, size=n)
    # True rate is the raw probability shifted down -- an over-confident model.
    true_rate = np.clip(raw * 0.8, 0.02, 0.95)
    actual = (rng.uniform(size=n) < true_rate).astype(int)
    return raw, actual


def _fit_binary(n: int, seed: int = 7) -> MarketCalibrator:
    """Fit a binary market the way the backtester does: complementary
    yes/no labels. MarketCalibrator renormalizes across whatever labels it is
    handed, so calling it with a lone label would trivially return 1.0 --
    binary markets are always passed as a pair."""

    raw, actual = _synthetic_validation(n, seed)
    calibrator = MarketCalibrator()
    calibrator.fit(
        {"yes": raw, "no": 1 - raw},
        {"yes": actual, "no": 1 - actual},
    )
    return calibrator


def _calibrated_yes(calibrator: MarketCalibrator, p: float) -> float:
    return calibrator.calibrate({"yes": p, "no": 1 - p})["yes"]


def test_small_validation_split_preserves_distinct_inputs():
    """The defect this guards against: isotonic regression fitted on a few
    hundred samples produces wide plateaus, so genuinely different fixtures
    come out with identical probabilities and the ensemble's ranking work is
    thrown away after it was correctly done.

    Observed before the fix, on ~285 real validation matches: raw home-win
    values of 0.30, 0.35, 0.40 and 0.45 all mapped to exactly 0.42.
    """

    calibrator = _fit_binary(285)

    probes = [0.30, 0.35, 0.40, 0.45]
    outputs = [_calibrated_yes(calibrator, p) for p in probes]

    assert len(set(round(o, 6) for o in outputs)) == len(probes), (
        f"distinct inputs collapsed to shared outputs: {list(zip(probes, outputs))}"
    )


def test_calibration_is_monotonic():
    """Higher raw probability must never produce a lower calibrated one --
    otherwise calibration would reorder fixtures, which is strictly worse
    than leaving them uncalibrated."""

    calibrator = _fit_binary(285)

    probes = [0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75]
    outputs = [_calibrated_yes(calibrator, p) for p in probes]

    assert outputs == sorted(outputs), f"calibration reordered inputs: {outputs}"


def test_calibration_corrects_systematic_bias():
    """It still has to do its actual job: an over-confident model should come
    out less confident, not merely unchanged."""

    calibrator = _fit_binary(400)

    # The synthetic model overstates by ~25%; calibration should pull a
    # mid-range estimate down toward the true rate.
    assert _calibrated_yes(calibrator, 0.60) < 0.60


def test_large_validation_split_uses_isotonic():
    """Platt scaling is the small-sample fallback, not the permanent choice --
    with enough data isotonic is the better calibrator and should be used."""

    from sklearn.isotonic import IsotonicRegression

    calibrator = _fit_binary(MIN_SAMPLES_FOR_ISOTONIC + 50)

    assert isinstance(calibrator.regressors["yes"], IsotonicRegression)


def test_small_validation_split_uses_platt():
    calibrator = _fit_binary(285)

    assert isinstance(calibrator.regressors["yes"], PlattCalibrator)


def test_single_class_validation_split_passes_through():
    """If the validation window contains only one outcome there is nothing to
    learn, so calibration must not invent a mapping from it."""

    raw = np.linspace(0.2, 0.8, 50)
    calibrator = MarketCalibrator()
    calibrator.fit(
        {"yes": raw, "no": 1 - raw},
        {"yes": np.zeros(50), "no": np.ones(50)},
    )

    # Pass-through (subject to the floor/cap), not a collapse to a constant.
    assert _calibrated_yes(calibrator, 0.75) > _calibrated_yes(calibrator, 0.25)
