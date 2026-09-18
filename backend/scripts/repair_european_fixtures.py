#!/usr/bin/env python3
"""Remove a competition's fixtures so they can be imported again cleanly.

For when the importer attached ties to the wrong clubs. The stored rows cannot
be corrected in place: a match row keeps the club it resolved to, not the name
the provider sent, so there is nothing left to re-resolve. The fixtures have to
come back from the API.

Deleting is therefore the repair, and it is safe for this competition
specifically because European fixtures come from one source: re-running the
import restores every one of them, scores included.

Dry run by default. Nothing is deleted without --delete, and the dependent
rows are counted first -- a fixture with predictions or odds against it is
worth a second look before it goes.

    python scripts/repair_european_fixtures.py --league "UEFA Champions League"
    python scripts/repair_european_fixtures.py --league "UEFA Champions League" --delete
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import aliased

from app.db.models import (
    ChatMessage,
    EloHistory,
    LivePrediction,
    Match,
    MatchOdds,
    MatchView,
    Prediction,
    Team,
)
from app.db.session import SessionLocal

# Everything that points at a match, and has to go first. ChatMessage is
# handled separately: its reference is nullable, so the message survives.
DEPENDENTS = [
    ("predictions", Prediction),
    ("live predictions", LivePrediction),
    ("odds", MatchOdds),
    ("Elo history", EloHistory),
    ("match views", MatchView),
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--league", default="UEFA Champions League")
    parser.add_argument("--delete", action="store_true", help="Actually delete. Without this, nothing changes.")
    args = parser.parse_args()

    sys.stdout.reconfigure(line_buffering=True)

    db = SessionLocal()
    try:
        home, away = aliased(Team), aliased(Team)
        rows = db.execute(
            select(Match.id, Match.date, home.name, away.name, Match.status)
            .join(home, Match.home_team_id == home.id)
            .join(away, Match.away_team_id == away.id)
            .where(Match.league == args.league)
            .order_by(Match.date, Match.id)
        ).all()

        if not rows:
            print(f"No {args.league} fixtures stored. Nothing to repair.")
            return

        ids = [r[0] for r in rows]
        print(f"{args.league}: {len(ids)} fixtures\n")
        for _id, date, h, a, status in rows:
            print(f"  {_id:>7}  {date:%Y-%m-%d %H:%M}  {h} vs {a}  [{status}]")

        print("\nRows that point at them:")
        for label, model in DEPENDENTS:
            count = db.execute(
                select(func.count()).select_from(model).where(model.match_id.in_(ids))
            ).scalar() or 0
            print(f"  {label:<18} {count}")
        chats = db.execute(
            select(func.count()).select_from(ChatMessage).where(ChatMessage.context_match_id.in_(ids))
        ).scalar() or 0
        print(f"  {'chat references':<18} {chats} (kept, reference cleared)")

        if not args.delete:
            print("\nDry run. Re-run with --delete to remove them, then import again:")
            print(f'    python scripts/import_api_football.py --leagues "{args.league}"')
            return

        for label, model in DEPENDENTS:
            db.execute(delete(model).where(model.match_id.in_(ids)))
        db.execute(
            update(ChatMessage)
            .where(ChatMessage.context_match_id.in_(ids))
            .values(context_match_id=None)
        )
        db.execute(delete(Match).where(Match.id.in_(ids)))
        db.commit()

        print(f"\nDeleted {len(ids)} fixtures. Import them again:")
        print(f'    python scripts/import_api_football.py --leagues "{args.league}"')
    finally:
        db.close()


if __name__ == "__main__":
    main()
