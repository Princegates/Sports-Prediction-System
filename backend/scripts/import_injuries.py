#!/usr/bin/env python3
"""Fetch currently reported injuries/suspensions for every tracked league
(or a given subset) and store them against the matches they apply to.

Run before generate_predictions.py / bootstrap.py so a day's predictions can
actually see them -- app.prediction_service reads from the stored
PlayerAbsence table, it never calls the provider itself. Confirmed starting
lineups are a separate, later signal (they aren't known until ~60-75 minutes
before kickoff): see app.data.squad_ingest.run_lineup_check_from_settings,
which runs continuously in the backend's own process instead of on this
script's schedule.

The key comes from the settings panel, so this needs only DATABASE_URL. One
request per league.

    python scripts/import_injuries.py
    python scripts/import_injuries.py --leagues "English Premier League" "Spanish La Liga"
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.data.squad_ingest import import_injuries_from_settings
from app.db.session import SessionLocal


def _current_season_start(today: dt.date | None = None) -> int:
    today = today or dt.date.today()
    return today.year if today.month >= 7 else today.year - 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--leagues", nargs="+", default=None, help="Default: every league this project tracks")
    parser.add_argument("--season", type=int, default=None, help="Defaults to the current season")
    args = parser.parse_args()

    sys.stdout.reconfigure(line_buffering=True)

    db = SessionLocal()
    try:
        reports = import_injuries_from_settings(
            db, league_names=args.leagues, season=args.season or _current_season_start()
        )
        if not reports:
            print("No API-Football key configured -- nothing to do.")
            return

        for report in reports:
            print(
                f"{report.league}: {report.stored} absence(s) stored "
                f"({report.considered} reported, {len(report.unresolved_teams)} unresolved team name(s))"
            )
            for name in report.unresolved_teams:
                print(f"    unresolved: {name!r}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
