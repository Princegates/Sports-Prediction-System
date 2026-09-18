#!/usr/bin/env python3
"""Import real historical results AND real current-season fixtures (played
and unplayed) from openfootball/football.json -- no API key, works anywhere
that can reach raw.githubusercontent.com.

Example:
    python scripts/fetch_openfootball_data.py --league "English Premier League" \\
        --seasons 2022-23 2023-24 2024-25 2025-26 2026-27
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests

from app.data.ingest import import_openfootball_season
from app.data.providers.openfootball import LEAGUE_FILE_CODES
from app.db.migrate import init_db
from app.db.session import SessionLocal, engine


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--league", required=True, choices=sorted(LEAGUE_FILE_CODES))
    parser.add_argument("--seasons", nargs="+", required=True, help='openfootball season folders, e.g. 2024-25 2025-26 2026-27')
    args = parser.parse_args()

    init_db(engine)
    db = SessionLocal()
    try:
        for season in args.seasons:
            try:
                result = import_openfootball_season(db, args.league, season)
            except requests.exceptions.HTTPError as exc:
                print(f"[{args.league} {season}] not available upstream yet ({exc.response.status_code}), skipping")
                continue
            print(f"[{args.league} {season}] inserted {result['inserted']}, updated {result['updated']}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
