#!/usr/bin/env python3
"""One-off fix for data already imported before the team-name normalization
fix (app/data/team_matching.py) existed: finds teams within the same league
whose names are naming variants of each other (e.g. "Real Madrid" and "Real
Madrid CF"), merges them into whichever one has more matches on record, and
re-points every match to the merged team. Safe to run repeatedly -- it's a
no-op once there's nothing left to merge.

Example:
    python scripts/merge_duplicate_teams.py
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.data.team_matching import normalize_team_name
from app.db.models import Match, Team
from app.db.session import SessionLocal


def main() -> None:
    db = SessionLocal()
    try:
        teams = list(db.execute(select(Team)).scalars())
        groups: dict[tuple[str, str], list[Team]] = defaultdict(list)
        for t in teams:
            groups[(t.league, normalize_team_name(t.name))].append(t)

        merged_count = 0
        for (league, _key), group in groups.items():
            if len(group) < 2:
                continue

            counts = {
                t.id: db.execute(
                    select(Match.id).where((Match.home_team_id == t.id) | (Match.away_team_id == t.id))
                ).all()
                for t in group
            }
            canonical = max(group, key=lambda t: len(counts[t.id]))
            others = [t for t in group if t.id != canonical.id]

            print(f"[{league}] merging {[o.name for o in others]} into {canonical.name!r}")

            for other in others:
                db.execute(
                    Match.__table__.update().where(Match.home_team_id == other.id).values(home_team_id=canonical.id)
                )
                db.execute(
                    Match.__table__.update().where(Match.away_team_id == other.id).values(away_team_id=canonical.id)
                )
                merged_aliases = set(canonical.aliases) | set(other.aliases) | {other.name}
                canonical.aliases = sorted(merged_aliases)
                db.delete(other)
                merged_count += 1

        db.commit()
        print(f"Done. Merged {merged_count} duplicate team record(s).")
        if merged_count:
            print(
                "Re-run scripts/backtest.py for each affected league -- it rebuilds Elo history "
                "and retrains from the now-merged match data, clearing out any stale ratings tied "
                "to the old (deleted) team records."
            )
    finally:
        db.close()


if __name__ == "__main__":
    main()
