#!/usr/bin/env python3
"""Merge fixtures that were duplicated by a kickoff that didn't match exactly.

Before ``import_fixtures`` learned to reconcile a moved kickoff onto the
existing SCHEDULED row instead of inserting a new one, every rescheduled
match in a league imported this way was inserted twice: once under its
original stored kickoff, once under the provider's new one. Both rows are
real column data -- correct teams, plausible dates -- so nothing about them
looks wrong in isolation, and the only way to find the pair is the same
football fact the audit script uses: a club cannot play the same opponent,
in the same direction, twice within a season inside one round-robin phase.

The rule for which row survives mirrors what the fixed importer now does
going forward, so a repair here and a fresh import from here on land on the
same row: the **older** row (the lower id) is kept, its kickoff is updated
to the **newer** row's date -- the provider's, and therefore the current
one -- and the newer row is deleted after anything pointing at it is moved
onto the survivor first.

Dry run by default. Nothing is written without --delete, and every pair
prints both rows plus what points at the one that would be removed.

    python scripts/repair_rescheduled_duplicates.py --league "English Premier League"
    python scripts/repair_rescheduled_duplicates.py --league "English Premier League" --delete
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import aliased

from app.db.models import ChatMessage, EloHistory, LivePrediction, Match, MatchOdds, MatchView, Prediction, Team
from app.db.session import SessionLocal

# Same window import_fixtures now uses to recognise a reschedule rather than
# a new fixture -- kept identical on purpose, so this repair finds exactly
# what the fix would have prevented, no more and no less.
RESCHEDULE_WINDOW = dt.timedelta(days=7)

DEPENDENTS = [
    ("predictions", Prediction),
    ("live predictions", LivePrediction),
    ("odds", MatchOdds),
    ("Elo history", EloHistory),
    ("match views", MatchView),
]


def find_pairs(db, league: str) -> list[tuple[Match, Match]]:
    home, away = aliased(Team), aliased(Team)
    rows = list(
        db.execute(
            select(Match)
            .where(Match.league == league, Match.status == "SCHEDULED")
            .order_by(Match.home_team_id, Match.away_team_id, Match.id)
        ).scalars()
    )

    by_pair: dict[tuple[int, int], list[Match]] = {}
    for m in rows:
        by_pair.setdefault((m.home_team_id, m.away_team_id), []).append(m)

    pairs: list[tuple[Match, Match]] = []
    for matches in by_pair.values():
        if len(matches) < 2:
            continue
        # Sorted by id already (query order), so [0] is the older row. More
        # than two sharing a pair within the window would be a third
        # duplicate, not a normal case -- handled by repeatedly pairing the
        # oldest survivor with the next one still in range.
        matches = sorted(matches, key=lambda m: m.id)
        i = 0
        while i < len(matches) - 1:
            older, newer = matches[i], matches[i + 1]
            if abs(newer.date - older.date) <= RESCHEDULE_WINDOW:
                pairs.append((older, newer))
                i += 2
            else:
                i += 1
    return pairs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--league", required=True)
    parser.add_argument("--delete", action="store_true", help="Actually merge. Without this, nothing changes.")
    args = parser.parse_args()

    sys.stdout.reconfigure(line_buffering=True)

    db = SessionLocal()
    try:
        pairs = find_pairs(db, args.league)
        if not pairs:
            print(f"No duplicate pairs found for {args.league}.")
            return

        print(f"{args.league}: {len(pairs)} duplicate pair(s)\n")

        home, away = aliased(Team), aliased(Team)
        names = dict(
            db.execute(
                select(Team.id, Team.name).where(
                    Team.id.in_({t for o, n in pairs for t in (o.home_team_id, o.away_team_id)})
                )
            ).all()
        )

        newer_ids = [newer.id for _, newer in pairs]
        dependent_counts: dict[str, int] = {}
        for label, model in DEPENDENTS:
            count = (
                db.execute(select(func.count()).select_from(model).where(model.match_id.in_(newer_ids))).scalar()
                or 0
            )
            dependent_counts[label] = count

        for older, newer in pairs:
            print(
                f"  {names[older.home_team_id]} vs {names[older.away_team_id]}: "
                f"keep #{older.id} ({older.date:%Y-%m-%d %H:%M}), "
                f"merge #{newer.id} ({newer.date:%Y-%m-%d %H:%M}) into it"
            )

        print("\nRows on the removed side that point at them:")
        for label, count in dependent_counts.items():
            print(f"  {label:<18} {count}")

        if not args.delete:
            print("\nDry run. Re-run with --delete to merge them.")
            return

        for older, newer in pairs:
            older.date = newer.date
            for _, model in DEPENDENTS:
                db.execute(update(model).where(model.match_id == newer.id).values(match_id=older.id))
            db.execute(update(ChatMessage).where(ChatMessage.context_match_id == newer.id).values(context_match_id=older.id))
            db.execute(delete(Match).where(Match.id == newer.id))

        db.commit()
        print(f"\nMerged {len(pairs)} pair(s).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
