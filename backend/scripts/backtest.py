#!/usr/bin/env python3
"""Temporal train/validation/test pipeline (spec sections 39-41).

Trains the Elo ratings and Gradient Boosting model on the earliest slice of
data, fits probability calibrators on a validation slice the models never
trained on, and reports accuracy / log loss / Brier score / calibration on a
held-out test slice that comes chronologically *after* both -- so no future
match ever leaks into a prediction for an earlier one.

Example:
    python scripts/backtest.py --league E0 --seasons 2021 2122 2223 2324 2425
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from sklearn.metrics import accuracy_score, log_loss

from app.data.providers.football_data_co_uk import LEAGUE_CODES
from app.db.models import Match, ModelMetric
from app.db.migrate import init_db
from app.db.session import SessionLocal, engine
from app.model_store import calibrator_path, ml_model_path
from app.prediction_models import elo
from app.prediction_models.calibration import MarketCalibrator
from app.prediction_models.ensemble import generate_prediction
from app.prediction_models.ml_model import MLModel, build_training_dataset
from sqlalchemy import select

RESULT_LABELS = ["H", "D", "A"]
# sklearn's log_loss assumes probability columns follow the lexicographic
# order of the labels it's given, regardless of the order you pass them in
# -- so we build the probability matrix in this order specifically for it.
RESULT_LABELS_SORTED = sorted(RESULT_LABELS)


def multiclass_brier(probs: list[dict[str, float]], actual: list[str]) -> float:
    total = 0.0
    for p, y in zip(probs, actual):
        total += sum((p.get(label, 0.0) - (1.0 if label == y else 0.0)) ** 2 for label in RESULT_LABELS)
    return total / len(actual)


def binary_brier(probs: list[float], actual: list[int]) -> float:
    return float(np.mean([(p - y) ** 2 for p, y in zip(probs, actual)]))


def expected_calibration_error(probs: list[float], actual: list[int], n_bins: int = 10) -> float:
    probs_arr = np.array(probs)
    actual_arr = np.array(actual)
    bins = np.linspace(0, 1, n_bins + 1)
    ece = 0.0
    for i in range(n_bins):
        mask = (probs_arr >= bins[i]) & (probs_arr < bins[i + 1] if i < n_bins - 1 else probs_arr <= bins[i + 1])
        if mask.sum() == 0:
            continue
        bucket_conf = probs_arr[mask].mean()
        bucket_acc = actual_arr[mask].mean()
        ece += (mask.sum() / len(probs_arr)) * abs(bucket_conf - bucket_acc)
    return float(ece)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--league", default="E0", choices=sorted(LEAGUE_CODES), help="football-data.co.uk code (ignored if --league-name is given)")
    parser.add_argument("--league-name", default=None, help='Exact league name already in the DB, e.g. "English Premier League" (use this for data imported via fetch_openfootball_data.py)')
    parser.add_argument("--train-fraction", type=float, default=0.70)
    parser.add_argument("--validation-fraction", type=float, default=0.15)
    args = parser.parse_args()

    league_name = args.league_name or LEAGUE_CODES[args.league]
    init_db(engine)
    db = SessionLocal()

    try:
        print(f"Rebuilding Elo history for {league_name} ...")
        elo.rebuild_elo_history(db, league_name)

        matches = list(
            db.execute(
                select(Match).where(Match.league == league_name, Match.home_score.is_not(None)).order_by(Match.date.asc())
            ).scalars()
        )
        if len(matches) < 100:
            print(f"Only {len(matches)} finished matches found -- import more seasons with fetch_historical_data.py first.")
            return

        n = len(matches)
        train_end_idx = int(n * args.train_fraction)
        val_end_idx = int(n * (args.train_fraction + args.validation_fraction))
        train_end_date = matches[train_end_idx].date
        val_end_date = matches[val_end_idx].date

        print(f"Train: < {train_end_date.date()}  |  Validation: {train_end_date.date()} -> {val_end_date.date()}  |  Test: >= {val_end_date.date()}")

        print("Building training features + fitting Gradient Boosting model ...")
        train_df = build_training_dataset(db, league_name, end_date=train_end_date)
        ml_model = MLModel()
        ml_model.fit(train_df)
        ml_model.save(ml_model_path(league_name))
        print(f"  trained on {len(train_df)} matches")

        validation_matches = [m for m in matches if train_end_date <= m.date < val_end_date]
        test_matches = [m for m in matches if m.date >= val_end_date]

        print(f"Fitting calibrators on {len(validation_matches)} validation matches ...")
        val_1x2_probs: dict[str, list[float]] = {"H": [], "D": [], "A": []}
        val_1x2_actual: dict[str, list[int]] = {"H": [], "D": [], "A": []}
        val_over25_probs, val_over25_actual = [], []
        val_btts_probs, val_btts_actual = [], []

        for m in validation_matches:
            result = generate_prediction(db, m.home_team_id, m.away_team_id, league_name, m.date, ml_model=ml_model)
            actual_result = "H" if m.home_score > m.away_score else ("D" if m.home_score == m.away_score else "A")
            for label, p in zip(RESULT_LABELS, [result.home_win, result.draw, result.away_win]):
                val_1x2_probs[label].append(p)
                val_1x2_actual[label].append(1 if label == actual_result else 0)

            val_over25_probs.append(result.over_probabilities["2.5"])
            val_over25_actual.append(1 if (m.home_score + m.away_score) > 2.5 else 0)
            val_btts_probs.append(result.btts_yes)
            val_btts_actual.append(1 if (m.home_score >= 1 and m.away_score >= 1) else 0)

        calibrator_1x2 = MarketCalibrator()
        calibrator_1x2.fit({k: np.array(v) for k, v in val_1x2_probs.items()}, {k: np.array(v) for k, v in val_1x2_actual.items()})
        calibrator_1x2.save(calibrator_path(league_name, "1x2"))

        calibrator_over25 = MarketCalibrator()
        calibrator_over25.fit(
            {"yes": np.array(val_over25_probs), "no": 1 - np.array(val_over25_probs)},
            {"yes": np.array(val_over25_actual), "no": 1 - np.array(val_over25_actual)},
        )
        calibrator_over25.save(calibrator_path(league_name, "over_2_5"))

        calibrator_btts = MarketCalibrator()
        calibrator_btts.fit(
            {"yes": np.array(val_btts_probs), "no": 1 - np.array(val_btts_probs)},
            {"yes": np.array(val_btts_actual), "no": 1 - np.array(val_btts_actual)},
        )
        calibrator_btts.save(calibrator_path(league_name, "btts"))

        print(f"Evaluating on {len(test_matches)} held-out test matches ...")
        calibrators = {"1x2": calibrator_1x2, "over_2_5": calibrator_over25, "btts": calibrator_btts}

        test_probs, test_actual = [], []
        test_over25_probs, test_over25_actual = [], []
        test_btts_probs, test_btts_actual = [], []

        for m in test_matches:
            result = generate_prediction(db, m.home_team_id, m.away_team_id, league_name, m.date, ml_model=ml_model, calibrators=calibrators)
            actual_result = "H" if m.home_score > m.away_score else ("D" if m.home_score == m.away_score else "A")
            test_probs.append({"H": result.home_win, "D": result.draw, "A": result.away_win})
            test_actual.append(actual_result)
            test_over25_probs.append(result.over_probabilities["2.5"])
            test_over25_actual.append(1 if (m.home_score + m.away_score) > 2.5 else 0)
            test_btts_probs.append(result.btts_yes)
            test_btts_actual.append(1 if (m.home_score >= 1 and m.away_score >= 1) else 0)

        predicted_labels = [max(p, key=p.get) for p in test_probs]
        accuracy = accuracy_score(test_actual, predicted_labels)
        ll = log_loss(
            test_actual,
            [[p[label] for label in RESULT_LABELS_SORTED] for p in test_probs],
            labels=RESULT_LABELS_SORTED,
        )
        brier = multiclass_brier(test_probs, test_actual)
        ece = expected_calibration_error([p["H"] for p in test_probs], [1 if a == "H" else 0 for a in test_actual])

        over25_ll = log_loss(test_over25_actual, test_over25_probs, labels=[0, 1])
        over25_brier = binary_brier(test_over25_probs, test_over25_actual)
        btts_ll = log_loss(test_btts_actual, test_btts_probs, labels=[0, 1])
        btts_brier = binary_brier(test_btts_probs, test_btts_actual)

        print("\n=== Test-set results ===")
        print(f"1X2 accuracy:        {accuracy:.3f}")
        print(f"1X2 log loss:        {ll:.3f}")
        print(f"1X2 Brier score:     {brier:.3f}  (lower is better)")
        print(f"Home-win calibration ECE: {ece:.3f}  (lower is better; 0 = perfectly calibrated)")
        print(f"Over 2.5 log loss:   {over25_ll:.3f}")
        print(f"Over 2.5 Brier:      {over25_brier:.3f}")
        print(f"BTTS log loss:       {btts_ll:.3f}")
        print(f"BTTS Brier:          {btts_brier:.3f}")

        metrics = [
            # Sample size is stored alongside the scores because a metric
            # without one is not interpretable -- 46% over 285 matches and
            # 46% over 12 are very different claims, and the API surfaces
            # these figures to users who can't see this script.
            ("n", float(len(test_matches))),
            ("accuracy", accuracy),
            ("log_loss", ll),
            ("brier_score", brier),
            ("calibration_error", ece),
            ("over_2_5_log_loss", over25_ll),
            ("over_2_5_brier", over25_brier),
            ("btts_log_loss", btts_ll),
            ("btts_brier", btts_brier),
        ]
        for name, value in metrics:
            db.add(ModelMetric(model_version="ensemble-v1", split="test", league=league_name, metric_name=name, metric_value=float(value)))
        db.commit()
        print("\nMetrics stored in model_metrics table.")

    finally:
        db.close()


if __name__ == "__main__":
    main()
