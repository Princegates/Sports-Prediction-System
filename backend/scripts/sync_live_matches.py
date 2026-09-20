#!/usr/bin/env python3
"""Poll API-Football's live board and reconcile it onto our own fixtures.

One request covers every match live anywhere in the world; TeamIndex.resolve
and the league-id filter narrow that down to the leagues this project holds,
the same as import_api_football.py does for the daily fixture pull.

Meant to run often -- every few minutes while matches are on -- since it
costs exactly one request per run no matter how many matches are live.

    python scripts/sync_live_matches.py
    python scripts/sync_live_matches.py --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.data.api_football_ingest import run_live_sync_from_settings
from app.data.providers.api_football import ApiFootballError, QuotaExceeded
from app.db.migrate import init_db
from app.db.session import SessionLocal, engine


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Print the plan and spend nothing")
    args = parser.parse_args()

    sys.stdout.reconfigure(line_buffering=True)

    if args.dry_run:
        print("Dry run -- nothing was requested and nothing was written.")
        return

    init_db(engine)
    db = SessionLocal()
    try:
        try:
            report = run_live_sync_from_settings(db)
        except QuotaExceeded as exc:
            print(f"stopped: {exc}", file=sys.stderr)
            raise SystemExit(1)
        except ApiFootballError as exc:
            print(f"failed: {exc}", file=sys.stderr)
            raise SystemExit(1)

        if report is None:
            # Unlike import_api_football.py, this runs unconditionally on a
            # schedule whether or not anyone opted in to a paid key -- so an
            # unset key is "nothing to do" here, not a misconfiguration to
            # fail loudly over every five minutes.
            print("No API-Football key saved -- live sync has nothing to do.")
            return

        print(f"{report.considered} live fixture(s) seen worldwide")
        print(f"  updated    : {report.updated}")
        print(f"  unchanged  : {report.unchanged}")
        print(f"  finished   : {report.finished}")
        if report.skipped_no_match:
            print(f"  no match   : {len(report.skipped_no_match)} (not yet imported, or not a tracked league)")
            for name in report.skipped_no_match[:10]:
                print(f"    {name}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
