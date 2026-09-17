"""Probability calibration (spec section 25).

Raw model/ensemble outputs are typically not perfectly calibrated -- e.g. of
all predictions where the model said "70%", the outcome might really occur
80% of the time. Isotonic regression fixes that mapping using a held-out
validation split (never the same matches the models were trained on).

Multiclass (1X2) probabilities are calibrated one-vs-rest per class and then
renormalized to sum to 1, which is the standard approach when a library
doesn't offer a native multiclass isotonic calibrator.
"""

from __future__ import annotations

from pathlib import Path

import joblib
import numpy as np
from sklearn.isotonic import IsotonicRegression


class MarketCalibrator:
    """One isotonic regressor per outcome label, e.g. {"H": ..., "D": ..., "A": ...}
    or {"yes": ...} for a binary market."""

    def __init__(self) -> None:
        self.regressors: dict[str, IsotonicRegression] = {}

    def fit(self, predicted: dict[str, np.ndarray], actual: dict[str, np.ndarray]) -> None:
        for label, probs in predicted.items():
            reg = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
            reg.fit(probs, actual[label])
            self.regressors[label] = reg

    def calibrate(self, raw_probs: dict[str, float]) -> dict[str, float]:
        if not self.regressors:
            return raw_probs

        calibrated = {}
        for label, p in raw_probs.items():
            reg = self.regressors.get(label)
            calibrated[label] = float(reg.predict([p])[0]) if reg else p

        total = sum(calibrated.values())
        if total > 0:
            calibrated = {k: v / total for k, v in calibrated.items()}
        return calibrated

    def save(self, path: str | Path) -> None:
        joblib.dump(self.regressors, path)

    @classmethod
    def load(cls, path: str | Path) -> "MarketCalibrator":
        instance = cls()
        instance.regressors = joblib.load(path)
        return instance
