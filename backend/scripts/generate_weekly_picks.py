#!/usr/bin/env python3
"""Builds this week's three system accumulators -- Low, Medium and High
risk, each 10-15 legs -- and publishes them to AI Picks' "This Week's Picks".

The tiers are per-leg probability bands (Low: every leg 85%+ likely, Medium
70-85%, High 55-70%), built by app.risk_picks from the predictions already in
the database -- no bookmaker odds needed. They used to be defined by combined
odds (5-10, 11-20, 21-30), which needed a captured price on every leg and so
published nothing whenever none were captured (the week they went missing:
"Only 0 qualifying match(es) available" for every tier).

The backend runs the same logic in-process every hour (see app.main), so
this is for the weekly retrain workflow's own step and for manual runs. It is
idempotent: a tier whose slip is still this week's and still fully upcoming
is left alone, so a run after the backend already built the week's slips
doesn't swap out accumulators members may have booked. ``--force`` rebuilds
them anyway.

    python scripts/generate_weekly_picks.py
    python scripts/generate_weekly_picks.py --dry-run
    python scripts/generate_weekly_picks.py --force
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.migrate import init_db
from app.db.session import SessionLocal, engine
from app.risk_picks import generate_weekly


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Report what would be written without saving it.")
    parser.add_argument("--force", action="store_true", help="Rebuild every tier even if this week's slip is current.")
    args = parser.parse_args()

    sys.stdout.reconfigure(line_buffering=True)

    init_db(engine)
    db = SessionLocal()
    try:
        for line in generate_weekly(db, dry_run=args.dry_run, force=args.force):
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
