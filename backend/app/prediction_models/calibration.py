"""Probability calibration (spec section 25).

Raw model/ensemble outputs are typically not perfectly calibrated -- e.g. of
all predictions where the model said "70%", the outcome might really occur
80% of the time. Calibration fixes that mapping using a held-out validation
split (never the same matches the models were trained on).

Multiclass (1X2) probabilities are calibrated one-vs-rest per class and then
renormalized to sum to 1, which is the standard approach when a library
doesn't offer a native multiclass calibrator.

## Why there are two methods

Isotonic regression is the better calibrator *given enough data*: it can fit
any monotonic mapping. But it is a **step function**, and on a small
validation split it produces very few, very wide steps -- which silently
destroys the model's ability to distinguish between fixtures.

This was not hypothetical. Fitted on ~285 Premier League validation matches,
the isotonic calibrator produced only 12 distinct output levels for home win
and 5 for draw. Raw home-win probabilities of 0.30, 0.35, 0.40 and 0.45 all
mapped to exactly 0.42; raw draw probabilities from 0.35 to 0.75 all mapped to
0.50. The consequence was that genuinely different fixtures came out with
byte-identical probabilities, and the ensemble's ranking information was
thrown away after it had been correctly computed.

Platt scaling (a one-dimensional logistic fit) has two parameters instead of
an arbitrary step function. It is smooth and strictly monotonic, so it can
correct systematic bias without ever collapsing two different inputs to the
same output. It is less expressive than isotonic and that is precisely why it
is the right default on small samples.

So: isotonic above ``MIN_SAMPLES_FOR_ISOTONIC``, Platt scaling below it. The
threshold is deliberately conservative -- the usual guidance is that isotonic
needs on the order of a thousand samples to beat Platt scaling.

**If you change anything here**, check the result against a *ranking* metric
(AUC, or simply the variance of the calibrated outputs) as well as a
calibration metric. Plateaus look perfectly healthy on a reliability diagram
while making per-fixture predictions useless.
"""

from __future__ import annotations

import logging
from pathlib import Path

import joblib
import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

logger = logging.getLogger(__name__)

# Isotonic regression can legitimately fit a hard 0 (or 1) at the extremes
# when the validation split happens to contain zero occurrences of a rare
# outcome down there. Football has no truly impossible or certain outcome
# short of the match not being played, so every calibrated probability is
# floored/capped away from the edges (spec section 62: never represent an
# outcome as a guarantee -- that includes an implicit "impossible").
MIN_PROB = 0.01
MAX_PROB = 0.99

# Below this many validation samples for a label, use Platt scaling instead
# of isotonic regression. See the module docstring for the reasoning.
MIN_SAMPLES_FOR_ISOTONIC = 1000


class PlattCalibrator:
    """Platt scaling: a logistic fit of the outcome against the raw
    probability's log-odds.

    Fitting in log-odds space rather than on the raw probability keeps the
    mapping close to the identity when the model is already well calibrated,
    which is the behaviour you want from a corrective step.
    """

    def __init__(self) -> None:
        self.model: LogisticRegression | None = None

    @staticmethod
    def _logit(p: np.ndarray) -> np.ndarray:
        clipped = np.clip(p, 1e-6, 1 - 1e-6)
        return np.log(clipped / (1 - clipped)).reshape(-1, 1)

    def fit(self, probs: np.ndarray, actual: np.ndarray) -> "PlattCalibrator":
        # A single-class validation split carries no information about the
        # mapping; leaving self.model as None makes calibrate() a pass-through
        # rather than fitting a degenerate constant.
        if len(np.unique(actual)) < 2:
            logger.warning("Platt calibration skipped: validation split has only one outcome class")
            return self

        self.model = LogisticRegression(solver="lbfgs")
        self.model.fit(self._logit(np.asarray(probs, dtype=float)), np.asarray(actual, dtype=int))
        return self

    def predict(self, probs: np.ndarray) -> np.ndarray:
        values = np.asarray(probs, dtype=float)
        if self.model is None:
            return values
        return self.model.predict_proba(self._logit(values))[:, 1]


class MarketCalibrator:
    """One calibrator per outcome label, e.g. {"H": ..., "D": ..., "A": ...}
    or {"yes": ...} for a binary market.

    Each label independently gets isotonic regression or Platt scaling
    depending on how much validation data it had, so a market with plenty of
    history isn't held back by one that doesn't.
    """

    def __init__(self) -> None:
        self.regressors: dict[str, IsotonicRegression | PlattCalibrator] = {}

    def fit(self, predicted: dict[str, np.ndarray], actual: dict[str, np.ndarray]) -> None:
        for label, probs in predicted.items():
            probs = np.asarray(probs, dtype=float)
            targets = np.asarray(actual[label], dtype=float)

            if len(probs) >= MIN_SAMPLES_FOR_ISOTONIC:
                reg = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
                reg.fit(probs, targets)
                self.regressors[label] = reg
            else:
                logger.info(
                    "Calibrating %r with Platt scaling (%d validation samples, isotonic needs %d) "
                    "-- isotonic would collapse distinct fixtures onto shared plateaus at this size",
                    label,
                    len(probs),
                    MIN_SAMPLES_FOR_ISOTONIC,
                )
                self.regressors[label] = PlattCalibrator().fit(probs, targets)

    def calibrate(self, raw_probs: dict[str, float]) -> dict[str, float]:
        if not self.regressors:
            return raw_probs

        calibrated = {}
        for label, p in raw_probs.items():
            reg = self.regressors.get(label)
            value = float(reg.predict(np.array([p]))[0]) if reg else p
            calibrated[label] = min(max(value, MIN_PROB), MAX_PROB)

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
