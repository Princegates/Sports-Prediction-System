#!/usr/bin/env python3
"""CLI entry point for app.random_picks -- see that module for the actual
draw-and-publish logic, shared with app.main's own in-process scheduler
(the daily cadence runs there too now; see its docstring for why).

Kept here for manual runs and --dry-run previews, and as what
refresh-predictions.yml's own "Generate today's random picks" / "Generate
this week's system picks" steps still call -- harmless, idempotent
overwrite either way, whenever that scheduled run actually fires.

    python scripts/generate_random_picks.py --which daily
    python scripts/generate_random_picks.py --which weekly
    python scripts/generate_random_picks.py --which both --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.migrate import init_db
from app.db.session import SessionLocal, engine
from app.random_picks import CADENCES, generate_cadence


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--which", choices=["daily", "weekly", "both"], default="both")
    parser.add_argument("--dry-run", action="store_true", help="Report what would be written without saving it.")
    args = parser.parse_args()

    sys.stdout.reconfigure(line_buffering=True)

    cadences = list(CADENCES) if args.which == "both" else [args.which]

    init_db(engine)
    db = SessionLocal()
    try:
        for cadence in cadences:
            for line in generate_cadence(db, cadence, dry_run=args.dry_run):
                print(line)

        if args.dry_run:
            db.rollback()
            print("\nDry run -- nothing written.")
        else:
            db.commit()
            print("\nSaved.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
