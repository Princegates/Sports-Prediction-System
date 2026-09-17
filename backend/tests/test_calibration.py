import numpy as np

from app.prediction_models.calibration import MAX_PROB, MIN_PROB, MarketCalibrator


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
