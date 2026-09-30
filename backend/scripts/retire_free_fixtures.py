#!/usr/bin/env python3
"""Fold what the free fixture feeds stored into API-Football's own rows.

See app/data/source_cleanup.py for how a duplicate is recognised and what
happens to it. The daily refresh (scripts/bootstrap.py) runs this with
--apply after each import; on its own it is a dry run that prints what it
would change.

    python scripts/retire_free_fixtures.py --leagues "Spanish La Liga" "Portuguese Primeira Liga"
    python scripts/retire_free_fixtures.py --leagues "Spanish La Liga" --apply
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.data.source_cleanup import retire_free_fixtures
from app.db.migrate import init_db
from app.db.session import SessionLocal, engine


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--leagues", nargs="+", required=True)
    parser.add_argument("--apply", action="store_true", help="Write the changes. Without this, nothing changes.")
    parser.add_argument("--changed-leagues-file", type=Path, default=None,
                        help="Write the leagues that changed here, one per line -- bootstrap.py rebuilds their "
                             "predictions, since a merged club's old ones were built on half its history.")
    args = parser.parse_args()

    sys.stdout.reconfigure(line_buffering=True)

    init_db(engine)
    db = SessionLocal()
    changed: list[str] = []
    try:
        for league in args.leagues:
            report = retire_free_fixtures(db, league, apply=args.apply)
            if report.changed:
                changed.append(league)
            print(f"\n=== {league} ===")
            if not report.changed and not report.left_alone and not report.conflicts:
                print("  nothing to do")
                continue
            for old, new in report.teams_merged:
                print(f"  club merged : {old!r} -> {new!r}")
            print(f"  fixtures    : {report.fixtures_merged} merged into API-Football's row, "
                  f"{report.fixtures_removed} removed (not on API-Football's calendar)")
            for line in report.conflicts:
                print(f"  not merged  : {line}")
            for line in report.left_alone[:20]:
                print(f"  kept        : {line}")
            if len(report.left_alone) > 20:
                print(f"  kept        : ... and {len(report.left_alone) - 20} more")
        if not args.apply:
            print("\nDry run -- re-run with --apply to write these changes.")
        elif args.changed_leagues_file:
            args.changed_leagues_file.write_text("".join(f"{lg}\n" for lg in changed))
    finally:
        db.close()


if __name__ == "__main__":
    main()
