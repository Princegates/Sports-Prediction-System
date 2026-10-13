#!/usr/bin/env python3
"""Read-only check of how much of the database has corners data, by league.

One-off verification for the enrich_match_stats.py backfill -- confirms the
run actually wrote home_corners/away_corners rather than just reporting a
high match rate.

    python scripts/check_corners_coverage.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select

from app.db.models import Match
from app.db.session import SessionLocal


def main() -> None:
    db = SessionLocal()
    try:
        rows = db.execute(
            select(
                Match.league,
                func.count(),
                func.count(Match.home_corners),
                func.avg(Match.home_corners + Match.away_corners),
            ).group_by(Match.league).order_by(Match.league)
        ).all()

        total = with_corners = 0
        for league, count, has_corners, avg_total in rows:
            total += count
            with_corners += has_corners
            avg_text = f"{avg_total:.2f}" if avg_total is not None else "n/a"
            print(f"{league:<30} {has_corners:>5}/{count:<5} ({has_corners / count:.0%})  avg total corners: {avg_text}")

        print(f"\n{'=' * 60}")
        print(f"Overall: {with_corners}/{total} matches have corners data ({with_corners / total:.1%})")
    finally:
        db.close()


if __name__ == "__main__":
    main()
