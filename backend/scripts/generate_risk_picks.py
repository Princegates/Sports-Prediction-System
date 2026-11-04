#!/usr/bin/env python3
"""CLI entry point for app.risk_picks -- the daily Low / Medium / High risk
accumulators (10-15 legs each). The backend runs the same logic in-process
every hour (see app.main); this is for a manual run or a --dry-run preview.

    python scripts/generate_risk_picks.py --dry-run
    python scripts/generate_risk_picks.py --force     # replace today's slips now
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.migrate import init_db
from app.db.session import SessionLocal, engine
from app.risk_picks import generate_daily


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Report what would be written without saving it.")
    parser.add_argument("--force", action="store_true", help="Rebuild every tier even if today's slip is still current.")
    args = parser.parse_args()

    sys.stdout.reconfigure(line_buffering=True)

    init_db(engine)
    db = SessionLocal()
    try:
        for line in generate_daily(db, dry_run=args.dry_run, force=args.force):
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
