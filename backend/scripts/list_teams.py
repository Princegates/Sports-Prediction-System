#!/usr/bin/env python3
"""Read-only: list every stored Team row whose name contains a given
substring, across every league, with its match count.

Written to settle exactly what a "same name" collision looks like before
writing anything -- id, league, and match count are the three facts that
decide whether two rows are a straightforward duplicate to merge or two
genuinely different eras/divisions of the same club.

    python scripts/list_teams.py --like Coventry "Hull City" Ipswich
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, or_, select

from app.db.models import Match, Team
from app.db.session import SessionLocal


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--like", nargs="+", required=True, help="Substrings to search for, case-insensitive")
    args = parser.parse_args()

    sys.stdout.reconfigure(line_buffering=True)

    db = SessionLocal()
    try:
        stmt = select(Team).where(or_(*[Team.name.ilike(f"%{s}%") for s in args.like])).order_by(Team.name, Team.league)
        teams = list(db.execute(stmt).scalars())

        if not teams:
            print("No matching teams.")
            return

        for t in teams:
            matches = db.execute(
                select(func.count()).select_from(Match).where(
                    (Match.home_team_id == t.id) | (Match.away_team_id == t.id)
                )
            ).scalar() or 0
            print(f"  #{t.id:<6} {t.name!r:<30} league={t.league!r:<28} matches={matches:<4} aliases={t.aliases}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
