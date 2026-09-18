#!/usr/bin/env python3
"""Temporal train/validation/test pipeline (spec sections 39-41).

Trains the Elo ratings and Gradient Boosting model on the earliest slice of
data, fits probability calibrators on a validation slice the models never
trained on, and reports accuracy / log loss / Brier score / calibration on a
held-out test slice that comes chronologically *after* both -- so no future
match ever leaks into a prediction for an earlier one.

Two modes:

    python scripts/backtest.py --league-name "English Premier League"
        Legacy single-league mode: one model trained on that league's own
        ~1,300 matches alone.

    python scripts/backtest.py --pool-leagues
        Cross-league mode (see ROADMAP.md, "the single biggest lever left on
        accuracy"): one Gradient Boosting model trained across every known
        league's data (9,000+ matches) with a league identity feature,
        evaluated per league against its own held-out test set and its own
        calibrators. This is what app.model_store.load_ml_model picks up
        automatically once it's saved -- no serving-code changes needed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import datetime as dt

import numpy as np
from sklearn.metrics import accuracy_score, log_loss
from sqlalchemy import select

from app.data.providers.football_data_co_uk import LEAGUE_CODES
from app.db.migrate import init_db
from app.db.models import Match, ModelMetric
from app.db.session import SessionLocal, engine
from app.model_store import GLOBAL_MODEL_KEY, calibrator_path, ml_model_path, save_ensemble_weights
from app.prediction_models import elo
from app.prediction_models.calibration import MarketCalibrator
from app.prediction_models.ensemble import blend_1x2, breakdown_to_hda, fit_ensemble_weights, generate_prediction
from app.prediction_models.ml_model import KNOWN_LEAGUES, MLModel, build_pooled_training_dataset, build_training_dataset

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


def compute_split(matches: list[Match], train_fraction: float, validation_fraction: float) -> tuple[dt.datetime, dt.datetime]:
    n = len(matches)
    train_end_idx = int(n * train_fraction)
    val_end_idx = int(n * (train_fraction + validation_fraction))
    return matches[train_end_idx].date, matches[val_end_idx].date


def baseline_metrics(
    label_or_dist: str | dict[str, float], test_actual: list[str]
) -> tuple[float, float]:
    if isinstance(label_or_dist, str):
        probs = {"H": 0.0, "D": 0.0, "A": 0.0}
        probs[label_or_dist] = 1.0
    else:
        probs = label_or_dist
    preds = [max(probs, key=probs.get)] * len(test_actual)
    acc = accuracy_score(test_actual, preds)
    # log_loss needs a probability per class per row, clipped away from
    # exact 0/1 so a wrong single-shot guess isn't -inf.
    clipped = {k: min(max(v, 1e-4), 1 - 1e-4) for k, v in probs.items()}
    clipped = {k: v / sum(clipped.values()) for k, v in clipped.items()}
    row = [clipped[label] for label in RESULT_LABELS_SORTED]
    ll_baseline = log_loss(test_actual, [row] * len(test_actual), labels=RESULT_LABELS_SORTED)
    return acc, ll_baseline


def evaluate_league(
    db,
    league_name: str,
    ml_model: MLModel,
    matches: list[Match],
    train_end_date: dt.datetime,
    val_end_date: dt.datetime,
    model_version: str,
) -> dict:
    """Fits this league's own calibrators on its own validation slice, then
    scores the shared (or per-league) ``ml_model`` on its own held-out test
    slice. Returns a summary dict used for the final cross-league table."""

    validation_matches = [m for m in matches if train_end_date <= m.date < val_end_date]
    test_matches = [m for m in matches if m.date >= val_end_date]

    print(f"\n--- {league_name} ---")
    print(f"Train: < {train_end_date.date()}  |  Validation: {train_end_date.date()} -> {val_end_date.date()}  |  Test: >= {val_end_date.date()}")
    print(f"Fitting calibrators on {len(validation_matches)} validation matches ...")

    val_1x2_actual_labels: list[str] = []
    val_breakdowns: list[dict] = []
    val_over25_probs, val_over25_actual = [], []
    val_btts_probs, val_btts_actual = [], []

    for m in validation_matches:
        # Weights don't matter for this pass -- model_breakdown carries each
        # component's raw, unblended probabilities regardless of how
        # generate_prediction() would have combined them, which is exactly
        # what fitting this league's own weights needs below.
        result = generate_prediction(db, m.home_team_id, m.away_team_id, league_name, m.date, ml_model=ml_model)
        actual_result = "H" if m.home_score > m.away_score else ("D" if m.home_score == m.away_score else "A")
        val_1x2_actual_labels.append(actual_result)
        val_breakdowns.append(result.model_breakdown)

        val_over25_probs.append(result.over_probabilities["2.5"])
        val_over25_actual.append(1 if (m.home_score + m.away_score) > 2.5 else 0)
        val_btts_probs.append(result.btts_yes)
        val_btts_actual.append(1 if (m.home_score >= 1 and m.away_score >= 1) else 0)

    print("Fitting ensemble blend weights on the same validation matches ...")
    fitted_weights = fit_ensemble_weights(val_breakdowns, val_1x2_actual_labels)
    save_ensemble_weights(league_name, fitted_weights)
    print(f"  fitted weights: elo={fitted_weights.elo:.2f} poisson={fitted_weights.poisson:.2f} ml={fitted_weights.ml:.2f} (default was 0.30/0.35/0.35)")

    val_1x2_probs: dict[str, list[float]] = {"H": [], "D": [], "A": []}
    val_1x2_actual: dict[str, list[int]] = {"H": [], "D": [], "A": []}
    for breakdown, actual_result in zip(val_breakdowns, val_1x2_actual_labels):
        ml_hda = breakdown_to_hda(breakdown["ml"]) if breakdown.get("ml") else None
        blended = blend_1x2(breakdown_to_hda(breakdown["elo"]), breakdown_to_hda(breakdown["poisson"]), ml_hda, fitted_weights)
        for label in RESULT_LABELS:
            val_1x2_probs[label].append(blended[label])
            val_1x2_actual[label].append(1 if label == actual_result else 0)

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
        result = generate_prediction(db, m.home_team_id, m.away_team_id, league_name, m.date, ml_model=ml_model, calibrators=calibrators, weights=fitted_weights)
        actual_result = "H" if m.home_score > m.away_score else ("D" if m.home_score == m.away_score else "A")
        test_probs.append({"H": result.home_win, "D": result.draw, "A": result.away_win})
        test_actual.append(actual_result)
        test_over25_probs.append(result.over_probabilities["2.5"])
        test_over25_actual.append(1 if (m.home_score + m.away_score) > 2.5 else 0)
        test_btts_probs.append(result.btts_yes)
        test_btts_actual.append(1 if (m.home_score >= 1 and m.away_score >= 1) else 0)

    if not test_matches:
        print("  (no test matches -- skipping metrics)")
        return {"league": league_name, "n": 0}

    predicted_labels = [max(p, key=p.get) for p in test_probs]
    accuracy = accuracy_score(test_actual, predicted_labels)

    # --- Baselines -----------------------------------------------------
    # A model that can't beat these hasn't earned the "AI" label. Rates come
    # from the training set (what would actually have been known at the
    # time), not the test set itself, which would leak the answer.
    train_actual = [
        ("H" if m.home_score > m.away_score else ("D" if m.home_score == m.away_score else "A"))
        for m in matches
        if m.date < train_end_date
    ]
    train_rate = {label: train_actual.count(label) / len(train_actual) for label in RESULT_LABELS}
    majority_label = max(train_rate, key=train_rate.get)

    baselines = {
        "always_home": baseline_metrics("H", test_actual),
        "always_draw": baseline_metrics("D", test_actual),
        "always_away": baseline_metrics("A", test_actual),
        "majority_class": baseline_metrics(majority_label, test_actual),
        "training_rate": baseline_metrics(train_rate, test_actual),
    }

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

    print("=== Test-set results ===")
    print(f"1X2 accuracy:        {accuracy:.3f}")
    print(f"1X2 log loss:        {ll:.3f}")
    print(f"1X2 Brier score:     {brier:.3f}  (lower is better)")
    print(f"Home-win calibration ECE: {ece:.3f}  (lower is better; 0 = perfectly calibrated)")
    print(f"Over 2.5 log loss:   {over25_ll:.3f}")
    print(f"Over 2.5 Brier:      {over25_brier:.3f}")
    print(f"BTTS log loss:       {btts_ll:.3f}")
    print(f"BTTS Brier:          {btts_brier:.3f}")

    always_home_acc = baselines["always_home"][0]
    print("\n=== Baselines (same test set) -- the model must beat these ===")
    for name, (b_acc, b_ll) in baselines.items():
        edge = (accuracy - b_acc) * 100
        print(f"  {name:16s} accuracy={b_acc:.3f}  log_loss={b_ll:.3f}  (ensemble edge: {edge:+.1f} pts)")

    metrics = [
        # Sample size is stored alongside the scores because a metric
        # without one is not interpretable -- 46% over 285 matches and 46%
        # over 12 are very different claims, and the API surfaces these
        # figures to users who can't see this script.
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
        db.add(ModelMetric(model_version=model_version, split="test", league=league_name, metric_name=name, metric_value=float(value)))
    for name, (b_acc, b_ll) in baselines.items():
        db.add(ModelMetric(model_version=f"baseline-{name}", split="test", league=league_name, metric_name="accuracy", metric_value=float(b_acc)))
        db.add(ModelMetric(model_version=f"baseline-{name}", split="test", league=league_name, metric_name="log_loss", metric_value=float(b_ll)))
    db.commit()

    return {
        "league": league_name,
        "n": len(test_matches),
        "accuracy": accuracy,
        "log_loss": ll,
        "always_home_acc": always_home_acc,
        "edge": (accuracy - always_home_acc) * 100,
    }


def print_summary_table(summaries: list[dict]) -> None:
    scored = [s for s in summaries if s.get("n")]
    if not scored:
        return
    print("\n" + "=" * 70)
    print("SUMMARY (mirrors the table in ROADMAP.md)")
    print("=" * 70)
    print(f"{'League':<28} {'Accuracy':>9} {'Baseline':>9} {'Edge':>8} {'n':>6}")
    for s in sorted(scored, key=lambda s: -s["accuracy"]):
        print(f"{s['league']:<28} {s['accuracy']*100:>8.1f}% {s['always_home_acc']*100:>8.1f}% {s['edge']:>+7.1f} {s['n']:>6.0f}")
    mean_acc = sum(s["accuracy"] for s in scored) / len(scored) * 100
    mean_edge = sum(s["edge"] for s in scored) / len(scored)
    print("-" * 70)
    print(f"{'Mean':<28} {mean_acc:>8.1f}% {'':>9} {mean_edge:>+7.1f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--league", default="E0", choices=sorted(LEAGUE_CODES), help="football-data.co.uk code (ignored if --league-name is given)")
    parser.add_argument("--league-name", default=None, help='Exact league name already in the DB, e.g. "English Premier League" (use this for data imported via fetch_openfootball_data.py). Ignored with --pool-leagues.')
    parser.add_argument("--pool-leagues", action="store_true", help="Train one cross-league model instead of one model per league (see ROADMAP.md item 6)")
    parser.add_argument("--leagues", nargs="+", default=None, help="Leagues to pool + evaluate when --pool-leagues is set; defaults to every league in KNOWN_LEAGUES that has data")
    parser.add_argument("--train-fraction", type=float, default=0.70)
    parser.add_argument("--validation-fraction", type=float, default=0.15)
    args = parser.parse_args()

    init_db(engine)
    db = SessionLocal()

    try:
        if not args.pool_leagues:
            league_name = args.league_name or LEAGUE_CODES[args.league]
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

            train_end_date, val_end_date = compute_split(matches, args.train_fraction, args.validation_fraction)

            print("Building training features + fitting Gradient Boosting model ...")
            train_df = build_training_dataset(db, league_name, end_date=train_end_date)
            ml_model = MLModel()
            ml_model.fit(train_df)
            ml_model.save(ml_model_path(league_name))
            print(f"  trained on {len(train_df)} matches")

            evaluate_league(db, league_name, ml_model, matches, train_end_date, val_end_date, model_version="ensemble-v1")
            return

        # --- Cross-league pooled mode ---------------------------------------
        target_leagues = args.leagues or KNOWN_LEAGUES
        league_matches: dict[str, list[Match]] = {}
        league_splits: dict[str, tuple[dt.datetime, dt.datetime]] = {}

        for league_name in target_leagues:
            print(f"Rebuilding Elo history for {league_name} ...")
            elo.rebuild_elo_history(db, league_name)
            matches = list(
                db.execute(
                    select(Match).where(Match.league == league_name, Match.home_score.is_not(None)).order_by(Match.date.asc())
                ).scalars()
            )
            if len(matches) < 100:
                print(f"  only {len(matches)} finished matches -- skipping {league_name}")
                continue
            league_matches[league_name] = matches
            league_splits[league_name] = compute_split(matches, args.train_fraction, args.validation_fraction)

        if not league_matches:
            print("No league had enough data. Import more seasons first.")
            return

        train_end_dates = {lg: split[0] for lg, split in league_splits.items()}
        print(f"\nBuilding pooled training set across {len(league_matches)} leagues ...")
        pooled_df = build_pooled_training_dataset(db, train_end_dates)
        print(f"  pooled training set: {len(pooled_df)} matches (vs ~{len(pooled_df) // len(league_matches)} per league trained alone)")

        ml_model = MLModel()
        ml_model.fit(pooled_df)
        ml_model.save(ml_model_path(GLOBAL_MODEL_KEY))
        print(f"  saved global model to {ml_model_path(GLOBAL_MODEL_KEY)}")

        summaries = []
        for league_name, matches in league_matches.items():
            train_end_date, val_end_date = league_splits[league_name]
            summaries.append(
                evaluate_league(db, league_name, ml_model, matches, train_end_date, val_end_date, model_version="ensemble-v1-pooled")
            )

        print_summary_table(summaries)
        print("\nMetrics stored in model_metrics table.")

    finally:
        db.close()


if __name__ == "__main__":
    main()
