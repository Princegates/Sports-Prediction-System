#!/usr/bin/env python3
"""Merge Team rows that are the same real club, within one league.

Two ways this happens:

1. openfootball's own season files spell a club inconsistently across
   seasons ("Real Madrid" vs "Real Madrid CF") -- what this script was
   originally written for.
2. TeamIndex.resolve() refuses an ambiguous name and, for a domestic
   import, creates a brand-new row rather than guess -- and until it
   learned to prefer a same-league candidate, a club's own established row
   and its fresh stub tied against each other the moment a short form of
   its name ("Bournemouth") missed the exact-match fast path against the
   full one ("AFC Bournemouth"). Every fixture that club played from then
   on split across two identities: real prices and results, attached to a
   row with no history.

Grouped by mutual name similarity *within one league*, using the same
scorer and threshold TeamIndex itself refuses an ambiguous match with --
this must never merge a club's row in one division with its row in
another; those are two real, separate histories (see
test_a_promoted_clubs_old_division_row_no_longer_blocks_its_new_one in
test_api_football.py for exactly why). Survivor is whichever row has more
matches on record, tie-broken by the lower id -- the older, more
established row.

Dry run by default. Nothing is written without --delete; every run prints
which rows would merge into which survivor and what points at the ones
that would go: matches (as home and as away) and Elo history -- the
original version of this script moved matches but not Elo history, which
would have left orphaned rows pointing at a deleted team the moment a
merge actually ran.

A merge does not, by itself, deduplicate the *fixtures* this created --
the same real match, imported once under each identity, is now the same
two teams playing each other twice. Run
scripts/repair_rescheduled_duplicates.py for the affected league
afterward; it already merges exactly that pattern.

    python scripts/merge_duplicate_teams.py --league "English Premier League"
    python scripts/merge_duplicate_teams.py --league "English Premier League" --delete
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select, update

from app.data.api_football_ingest import _NAME_THRESHOLD
from app.data.team_matching import name_match_score
from app.db.models import EloHistory, Match, Team
from app.db.session import SessionLocal

DEPENDENTS = [
    ("matches (home)", Match, "home_team_id"),
    ("matches (away)", Match, "away_team_id"),
    ("Elo history", EloHistory, "team_id"),
]


def find_groups(db, league: str | None = None) -> list[list[Team]]:
    """Every set of >=2 Team rows in one league that are, by name, the same
    club -- scored the same way and against the same threshold TeamIndex
    itself uses to refuse a match, so this finds exactly what that refusal
    would have produced."""

    stmt = select(Team)
    if league:
        stmt = stmt.where(Team.league == league)
    teams = list(db.execute(stmt.order_by(Team.id)).scalars())

    by_league: dict[str, list[Team]] = defaultdict(list)
    for t in teams:
        by_league[t.league].append(t)

    groups: list[list[Team]] = []
    for league_teams in by_league.values():
        remaining = list(league_teams)
        while remaining:
            seed = remaining.pop(0)
            cluster = [seed]
            still_remaining = []
            for other in remaining:
                primary, _coverage = name_match_score(seed.name, other.name)
                if primary >= _NAME_THRESHOLD:
                    cluster.append(other)
                else:
                    still_remaining.append(other)
            remaining = still_remaining
            if len(cluster) >= 2:
                groups.append(cluster)

    return groups


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--league", default=None, help="Limit to one league. Default: every league.")
    parser.add_argument("--delete", action="store_true", help="Actually merge. Without this, nothing changes.")
    args = parser.parse_args()

    sys.stdout.reconfigure(line_buffering=True)

    db = SessionLocal()
    try:
        groups = find_groups(db, args.league)
        if not groups:
            scope = args.league or "any league"
            print(f"No duplicate club groups found for {scope}.")
            return

        print(f"{len(groups)} duplicate club group(s) found\n")

        all_ids = [t.id for group in groups for t in group]
        match_counts = dict(
            db.execute(
                select(Team.id, func.count(Match.id))
                .select_from(Team)
                .outerjoin(Match, (Match.home_team_id == Team.id) | (Match.away_team_id == Team.id))
                .where(Team.id.in_(all_ids))
                .group_by(Team.id)
            ).all()
        )

        plans: list[tuple[Team, list[Team]]] = []
        for group in groups:
            canonical = max(group, key=lambda t: (match_counts.get(t.id, 0), -t.id))
            others = [t for t in group if t.id != canonical.id]
            plans.append((canonical, others))

            other_desc = ", ".join(f"#{o.id} {o.name!r} ({match_counts.get(o.id, 0)} match(es))" for o in others)
            print(
                f"  [{canonical.league}] keep #{canonical.id} {canonical.name!r} "
                f"({match_counts.get(canonical.id, 0)} match(es)) <- merge {other_desc}"
            )

        other_ids = [o.id for _, others in plans for o in others]
        print("\nRows on the removed side that point at them:")
        for label, model, column in DEPENDENTS:
            count = (
                db.execute(select(func.count()).select_from(model).where(getattr(model, column).in_(other_ids))).scalar()
                or 0
            )
            print(f"  {label:<14} {count}")

        if not args.delete:
            print("\nDry run. Re-run with --delete to merge them.")
            print("Note: merging will likely surface same-match-twice fixture duplicates --")
            print("run scripts/repair_rescheduled_duplicates.py for this league afterward.")
            return

        merged_count = 0
        for canonical, others in plans:
            for other in others:
                for _label, model, column in DEPENDENTS:
                    db.execute(update(model).where(getattr(model, column) == other.id).values(**{column: canonical.id}))
                merged_aliases = set(canonical.aliases) | set(other.aliases) | {other.name}
                canonical.aliases = sorted(merged_aliases)
                db.delete(other)
                merged_count += 1

        db.commit()
        print(f"\nMerged {merged_count} duplicate team record(s).")
        print("\nNext steps:")
        print(f'  python scripts/repair_rescheduled_duplicates.py --league "{args.league or "<league>"}" --delete')
        print("  python scripts/backtest.py -- rebuilds Elo history and retrains from the")
        print("  now-merged match data, clearing out any stale ratings tied to the old")
        print("  (deleted) team records.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
