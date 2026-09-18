#!/usr/bin/env python3
"""Check that imported European fixtures were attached to the right clubs.

A European tie resolves to domestic club rows by name, and a name can fit two
clubs. "Atletico Madrid" scores a perfect match against "Real Madrid CF" --
"Real" and "CF" are stripped as club-name furniture, leaving the single token
"madrid", which "Atletico Madrid" accounts for in full. A resolver that ranks
on that number alone picks whichever row it saw first.

The result is invisible in the data: Real Madrid simply has a fixture it never
played, with a plausible opponent on a plausible date, and its Elo history
absorbs the result.

This finds it without needing the real calendar, from two facts about football
that hold regardless of competition:

  * a club cannot play twice on the same day;
  * in a league phase every club plays the same number of matches.

Both are violated in exactly the way a mis-attachment produces -- the club
that wrongly received a tie gains a fixture, the club that should have had it
is missing one.

Read-only. It reports and exits non-zero; nothing is deleted here, because
which row to remove is a judgement about real fixtures, not about names.

    python scripts/audit_european_fixtures.py
    python scripts/audit_european_fixtures.py --league "UEFA Europa League"
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from sqlalchemy.orm import aliased

from app.db.models import Match, Team
from app.db.session import SessionLocal


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--league", default="UEFA Champions League")
    parser.add_argument("--list", action="store_true", help="Print every fixture, not only the problems")
    args = parser.parse_args()

    sys.stdout.reconfigure(line_buffering=True)

    db = SessionLocal()
    try:
        home, away = aliased(Team), aliased(Team)
        rows = db.execute(
            select(Match.id, Match.date, home.name, home.league, away.name, away.league)
            .join(home, Match.home_team_id == home.id)
            .join(away, Match.away_team_id == away.id)
            .where(Match.league == args.league)
            .order_by(Match.date, Match.id)
        ).all()

        if not rows:
            print(f"No {args.league} fixtures stored.")
            return

        print(f"{args.league}: {len(rows)} fixtures stored\n")

        if args.list:
            for _id, date, h, _hl, a, _al in rows:
                print(f"  {date:%Y-%m-%d %H:%M}  {h} vs {a}")
            print()

        per_day: dict[tuple[str, object], list[int]] = defaultdict(list)
        appearances: Counter[str] = Counter()
        leagues: Counter[str] = Counter()

        for match_id, date, h, hl, a, al in rows:
            for club, league in ((h, hl), (a, al)):
                per_day[(club, date.date())].append(match_id)
                appearances[club] += 1
                leagues[league] += 1

        print("Clubs by domestic league:")
        for league, count in leagues.most_common():
            noun = "appearance" if count == 1 else "appearances"
            print(f"  {league:<26} {count:>4} {noun}")

        doubled = {key: ids for key, ids in per_day.items() if len(ids) > 1}
        if doubled:
            print(f"\nIMPOSSIBLE: {len(doubled)} club(s) play twice on one day.")
            print("A name that fits two clubs is the usual cause -- one of them was")
            print("given a tie it never played.")
            for (club, day), ids in sorted(doubled.items(), key=lambda kv: (kv[0][1], kv[0][0])):
                print(f"  {club} on {day}: match ids {', '.join(str(i) for i in ids)}")

        counts = Counter(appearances.values())
        print("\nFixtures per club:")
        for played, clubs in sorted(counts.items()):
            names = sorted(c for c, n in appearances.items() if n == played)
            shown = ", ".join(names[:6]) + (f", ... (+{len(names) - 6})" if len(names) > 6 else "")
            noun = "fixture " if played == 1 else "fixtures"
            print(f"  {played:>2} {noun}: {clubs:>2} club(s)  {shown}")

        if doubled:
            raise SystemExit(1)

        print("\nNo club plays twice in a day. Nothing here looks mis-attached.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
