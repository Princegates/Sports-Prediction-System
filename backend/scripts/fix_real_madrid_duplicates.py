#!/usr/bin/env python3
"""Fix the two Champions League rows where Real Madrid CF is stored as both
home and away team -- Match #10970 (2025-03-04) and #10976 (2025-03-12), the
2024-25 Round of 16 ties against Club Atlético de Madrid. See
scripts/inspect_real_madrid_duplicates.py's own findings: both rows are
sourced from 'football-data.co.uk' (which has never supplied Champions
League fixtures in this project -- a leftover from before API-Football
became the sole fixture source), both sit exactly between Real Madrid's
real, correctly-attributed La Liga fixtures on 2025-03-04 and 2025-03-12,
and "Club Atlético de Madrid" already has its own Team row (id 307) with
real La Liga history -- so this corrects the wrong side's team_id to that
row rather than deleting real, scored fixtures (neither row has any
reference in any other table, confirmed before this script was written).

Leg 1 was hosted at the Bernabéu (Real Madrid home) and leg 2 at the
Metropolitano (Atlético home) -- the real world fact this script's fix
reflects, not something inferred from the stored data alone.

Verifies each row still has the exact "Real Madrid vs Real Madrid" shape
before changing it, and refuses to touch a row that doesn't match --
written once, for these two rows specifically, not a general-purpose
repair tool.

    python scripts/fix_real_madrid_duplicates.py            # dry run
    python scripts/fix_real_madrid_duplicates.py --apply
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse

from sqlalchemy import select

from app.db.models import Match, Team
from app.db.session import SessionLocal

# (match_id, which side is actually Club Atlético de Madrid)
FIXES = [
    (10970, "away"),  # 2025-03-04, Real Madrid home leg -- away side is wrong
    (10976, "home"),  # 2025-03-12, Atlético home leg -- home side is wrong
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Write the change. Without this, nothing changes.")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        real_madrid = db.execute(select(Team).where(Team.name == "Real Madrid CF")).scalar_one()
        atletico = db.execute(select(Team).where(Team.name == "Club Atlético de Madrid")).scalar_one()
        print(f"Real Madrid CF: Team.id={real_madrid.id}")
        print(f"Club Atlético de Madrid: Team.id={atletico.id}\n")

        for match_id, wrong_side in FIXES:
            m = db.get(Match, match_id)
            if m is None:
                print(f"Match #{match_id}: not found -- skipping")
                continue

            if m.home_team_id != real_madrid.id or m.away_team_id != real_madrid.id:
                print(
                    f"Match #{match_id}: home_team_id={m.home_team_id}, away_team_id={m.away_team_id} -- "
                    "no longer 'Real Madrid vs Real Madrid', refusing to touch it"
                )
                continue

            print(f"Match #{match_id} ({m.date}): home={m.home_team_id}, away={m.away_team_id}")
            if args.apply:
                if wrong_side == "away":
                    m.away_team_id = atletico.id
                else:
                    m.home_team_id = atletico.id
                print(f"  -> home={m.home_team_id}, away={m.away_team_id}")
            else:
                proposed_home = atletico.id if wrong_side == "home" else m.home_team_id
                proposed_away = atletico.id if wrong_side == "away" else m.away_team_id
                print(f"  (dry run) would become home={proposed_home}, away={proposed_away}")

        if args.apply:
            db.commit()
            print("\nCommitted.")
        else:
            print("\nDry run -- nothing was written. Re-run with --apply to write it.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
