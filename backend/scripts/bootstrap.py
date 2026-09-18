#!/usr/bin/env python3
"""One command to take a fresh deployment from empty to serving predictions.

Runs the whole setup chain in order: schema, data import, model training and
backtest, prediction generation, first superadmin. Each step is idempotent, so
re-running it on an existing deployment tops up data and retrains rather than
duplicating anything.

This exists because doing it by hand is five commands that must run in the
right order, and getting the order wrong fails in ways that look like bugs --
generating predictions before training, for instance, silently produces
predictions with the gradient-boosting model missing from the blend.

    python scripts/bootstrap.py                      # defaults below
    python scripts/bootstrap.py --leagues "English Premier League" "Spanish La Liga"
    python scripts/bootstrap.py --skip-training      # results + predictions, no retrain

Training and prediction generation are separate steps, so a cheap daily
refresh (--skip-training) can reuse models an occasional retrain produced.
That split is not cosmetic: retraining reads every match in every league,
which a managed database bills as egress, and doing it nightly is what
exhausted a 5 GB monthly allowance in two days.

On a host, set SUPERADMIN_EMAIL and SUPERADMIN_PASSWORD in the environment and
the admin account is created non-interactively.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Seasons worth importing by default: enough history to train on, plus the
# current one so there are fixtures to predict.
DEFAULT_SEASONS = ["2021-22", "2022-23", "2023-24", "2024-25", "2025-26", "2026-27"]
DEFAULT_LEAGUES = ["English Premier League"]


def run(label: str, args: list[str], required: bool = True) -> bool:
    """Run a setup step, reporting clearly which one failed.

    Non-required steps warn and continue: a league whose season file doesn't
    exist upstream shouldn't abort a multi-league bootstrap that has already
    imported the others successfully.
    """

    print(f"\n{'=' * 68}\n>> {label}\n{'=' * 68}", flush=True)
    started = time.monotonic()
    result = subprocess.run([sys.executable, *args], cwd=ROOT)
    elapsed = time.monotonic() - started

    if result.returncode != 0:
        message = f"!! {label} failed (exit {result.returncode}) after {elapsed:.0f}s"
        if required:
            print(message, file=sys.stderr, flush=True)
            raise SystemExit(result.returncode)
        print(f"{message} -- continuing", file=sys.stderr, flush=True)
        return False

    print(f"-- {label} done in {elapsed:.0f}s", flush=True)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--leagues", nargs="+", default=DEFAULT_LEAGUES)
    parser.add_argument("--seasons", nargs="+", default=DEFAULT_SEASONS)
    parser.add_argument("--days-ahead", type=int, default=14)
    parser.add_argument("--skip-training", action="store_true", help="Import data but don't train or backtest")
    parser.add_argument("--skip-data", action="store_true", help="Train on data already imported")
    parser.add_argument("--skip-predictions", action="store_true", help="Import and train but don't generate predictions")
    args = parser.parse_args()

    print("Bootstrapping AI Football Prediction & Analytics System")
    print(f"  leagues: {', '.join(args.leagues)}")
    print(f"  seasons: {', '.join(args.seasons)}")

    # Import the app late so a missing dependency surfaces as a clear error
    # here rather than a traceback from inside a subprocess.
    from app.config import get_settings
    from app.db.migrate import init_db
    from app.db.session import engine

    settings = get_settings()
    shown = settings.normalized_database_url.split("@")[-1]  # never print credentials
    print(f"  database: {shown}")

    if settings.secret_key_is_default:
        print(
            "\n  WARNING: SECRET_KEY is still the published default. Session tokens can be\n"
            "           forged by anyone who has read this repository. Set it before going live.",
            file=sys.stderr,
        )

    print("\n>> Creating schema")
    init_db(engine)
    print("-- schema ready")

    if not args.skip_data:
        for league in args.leagues:
            run(
                f"Importing {league}",
                ["scripts/fetch_openfootball_data.py", "--league", league, "--seasons", *args.seasons],
                required=False,
            )

    # None when training didn't run this time (a daily refresh reusing models
    # a weekly retrain produced); True/False when it did. Predicting with a
    # model whose training just failed buries the failure behind numbers that
    # look perfectly plausible, so that case is skipped loudly.
    trained: bool | None = None
    if not args.skip_training:
        if len(args.leagues) > 1:
            # One cross-league model trained on all of them together beats N
            # models of N leagues each trained alone -- see ROADMAP.md item 6.
            trained = run(
                "Training + backtesting (cross-league)",
                ["scripts/backtest.py", "--pool-leagues", "--leagues", *args.leagues],
                required=False,
            )
        else:
            trained = run(
                f"Training + backtesting {args.leagues[0]}",
                ["scripts/backtest.py", "--league-name", args.leagues[0]],
                required=False,
            )

    if args.skip_predictions:
        pass
    elif trained is False:
        print(
            "\n!! Skipping prediction generation: training failed, so the models on disk are "
            "missing or stale.",
            file=sys.stderr,
            flush=True,
        )
    else:
        for league in args.leagues:
            run(
                f"Generating predictions for {league}",
                [
                    "scripts/generate_predictions.py",
                    "--league",
                    league,
                    "--days-ahead",
                    str(args.days_ahead),
                ],
                required=False,
            )

    admin_email = os.environ.get("SUPERADMIN_EMAIL")
    if admin_email:
        if not os.environ.get("SUPERADMIN_PASSWORD"):
            print(
                "\n!! SUPERADMIN_EMAIL is set but SUPERADMIN_PASSWORD is not -- skipping admin creation.",
                file=sys.stderr,
            )
        else:
            run("Creating superadmin", ["scripts/create_superadmin.py", "--email", admin_email], required=False)
    else:
        print(
            "\n>> No SUPERADMIN_EMAIL set -- create the first admin yourself with:\n"
            "     python scripts/create_superadmin.py --email you@example.com"
        )

    print("\nBootstrap complete. Start the API with:")
    print("     uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}")


if __name__ == "__main__":
    main()
