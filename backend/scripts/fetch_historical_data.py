#!/usr/bin/env python3
"""Import free historical results from football-data.co.uk.

Example:
    python scripts/fetch_historical_data.py --league E0 --seasons 2122 2223 2324 2425
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.data.ingest import import_historical_season
from app.data.providers.football_data_co_uk import LEAGUE_CODES
from app.db.migrate import init_db
from app.db.session import SessionLocal, engine


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--league", default="E0", choices=sorted(LEAGUE_CODES), help="football-data.co.uk league code")
    parser.add_argument("--seasons", nargs="+", required=True, help="e.g. 2223 2324 2425")
    args = parser.parse_args()

    init_db(engine)

    db = SessionLocal()
    try:
        total = 0
        for season in args.seasons:
            inserted = import_historical_season(db, args.league, season)
            print(f"[{args.league} {season}] inserted {inserted} matches")
            total += inserted
        print(f"Done. {total} new matches imported for {LEAGUE_CODES[args.league]}.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
