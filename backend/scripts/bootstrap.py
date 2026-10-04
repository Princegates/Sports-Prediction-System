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
import datetime as dt
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DEFAULT_LEAGUES = ["English Premier League"]

# Fixtures and results come from API-Football alone. The free feeds
# (openfootball, TheSportsDB) used to fill this step, and running both at once
# stored every match twice under two spellings of each club -- see
# app/data/source_cleanup.py. One request per league per season, so a daily
# refresh of every league costs about ten requests of a 7,500 daily budget.
API_FOOTBALL_MAX_REQUESTS = 200

# One request per league per day in the odds window (see import_odds's own
# docstring for why it's per-day rather than one request per league) --
# capped well above what any realistic --days-ahead actually costs, so this
# ceiling is never what stops a run; --max-requests on the odds subprocess
# itself is what protects the real daily budget if it ever is.
ODDS_MAX_REQUESTS = 300


def season_start_year(season: str) -> int:
    """API-Football names a season by the year it starts: "2025-26" -> 2025."""

    return int(season.split("-")[0])


def current_season_start(today: dt.date | None = None) -> int:
    today = today or dt.date.today()
    return today.year if today.month >= 7 else today.year - 1


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
    parser.add_argument("--seasons", nargs="+", default=None,
                        help="Seasons to import, e.g. 2025-26 or 2025. Default: the current season only; "
                             "pass earlier ones to backfill history on a fresh database.")
    parser.add_argument("--days-ahead", type=int, default=14)
    parser.add_argument("--skip-training", action="store_true", help="Import data but don't train or backtest")
    parser.add_argument("--skip-data", action="store_true", help="Train on data already imported")
    parser.add_argument("--skip-predictions", action="store_true", help="Import and train but don't generate predictions")
    args = parser.parse_args()

    print("Bootstrapping AI Football Prediction & Analytics System")
    print(f"  leagues: {', '.join(args.leagues)}")
    seasons = [season_start_year(s) for s in args.seasons] if args.seasons else [current_season_start()]
    print(f"  seasons: {', '.join(str(s) for s in seasons)}")

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

    cleaned_leagues: list[str] = []
    if not args.skip_data:
        from app.data.providers.api_football import LEAGUE_IDS

        api_leagues = [lg for lg in args.leagues if lg in LEAGUE_IDS]
        for league in args.leagues:
            if league not in LEAGUE_IDS:
                print(f"\n!! {league}: no API-Football league id known -- not imported", file=sys.stderr, flush=True)
        if api_leagues:
            run(
                "Importing fixtures and results from API-Football",
                [
                    "scripts/import_api_football.py",
                    "--leagues", *api_leagues,
                    "--seasons", *[str(s) for s in seasons],
                    "--max-requests", str(API_FOOTBALL_MAX_REQUESTS),
                ],
                required=False,
            )
            # A separate call, not --odds on the one above: that call's
            # --seasons can carry years of backfill on a seed run, and asking
            # for odds on every day of every one of those historical seasons
            # would multiply the request count by --days-ahead for data
            # nobody will ever price. Odds are only ever about fixtures that
            # haven't kicked off yet, so this always targets the current
            # season regardless of what --seasons was given, with its own
            # ceiling and its own failure (a quota hiccup here shouldn't be
            # read as the fixture import above having failed).
            run(
                "Capturing market odds",
                [
                    "scripts/import_api_football.py",
                    "--leagues", *api_leagues,
                    "--seasons", str(current_season_start()),
                    "--odds",
                    "--days-ahead", str(args.days_ahead),
                    "--max-requests", str(ODDS_MAX_REQUESTS),
                ],
                required=False,
            )
        # Folds what the free feeds stored into API-Football's rows, for every
        # league rather than just this run's: it spends no API requests, and a
        # league API-Football has never returned fixtures for is left exactly
        # as it is.
        changed_file = ROOT / ".retired_leagues.txt"
        changed_file.unlink(missing_ok=True)
        run(
            "Retiring duplicates left by the free fixture feeds",
            [
                "scripts/retire_free_fixtures.py", "--leagues", *LEAGUE_IDS, "--apply",
                "--changed-leagues-file", str(changed_file),
            ],
            required=False,
        )
        if changed_file.exists():
            # A merged club's upcoming predictions were built on half its
            # history -- rebuild them even for a league this run didn't import.
            cleaned_leagues = [lg for lg in changed_file.read_text().splitlines() if lg]
            changed_file.unlink()

    # None when training didn't run this time (a daily refresh reusing models
    # a weekly retrain produced); True/False when it did. Predicting with a
    # model whose training just failed buries the failure behind numbers that
    # look perfectly plausible, so that case is skipped loudly.
    trained: bool | None = None
    prediction_leagues = [*args.leagues, *(lg for lg in cleaned_leagues if lg not in args.leagues)]
    if not args.skip_training:
        # One ML model per league, not pooled. An earlier version of this ran
        # `backtest.py --pool-leagues` for the multi-league case, on the
        # reasoning in ROADMAP.md item 6 that one model with every league's
        # data behind it beats N data-starved ones. Measured, that was wrong:
        # pooling came out at 49.7% mean against 50.1% per-league, losing 3.8
        # points on the Premier League and 2.2 on La Liga. ROADMAP.md 1d has
        # the table and why.
        #
        # This is also the only place that produced ml_model_global.joblib,
        # which load_ml_model() used to prefer over every per-league model --
        # so leaving it here meant the weekly retrain quietly redeployed the
        # worse model, whatever the roadmap said.
        #
        # What IS shared is the blend weights, which --leagues fits once on
        # every league's validation matches together (model_store.
        # load_ensemble_weights has the measurements). That needs all the
        # leagues in one run, so they go together -- and only if that run
        # fails outright does each league retrain alone, blending with the
        # last saved weights, so one league's bad data can't stop the rest.
        trained_together = run(
            "Training + backtesting every league (shared blend weights)",
            ["scripts/backtest.py", "--leagues", *args.leagues],
            required=False,
        )
        results = [trained_together] if trained_together else [
            run(
                f"Training + backtesting {league}",
                ["scripts/backtest.py", "--league-name", league],
                required=False,
            )
            for league in args.leagues
        ]
        # False only if every league failed. One league missing its data
        # upstream shouldn't stop the others' predictions being generated.
        trained = any(results)

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
        for league in prediction_leagues:
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
