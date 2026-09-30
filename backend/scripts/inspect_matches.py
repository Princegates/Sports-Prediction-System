#!/usr/bin/env python3
"""Show where suspicious stored results came from. Read-only.

Two football facts flag a bad row without needing the real calendar: a club
can't play twice on the same day, and the same fixture (same clubs, same
direction) can't be played twice within a day. For every row either check
flags, this prints what the row itself says about its origin -- its
``source``, API-Football's fixture id if it has one, whether the live-board
sync ever touched it, and how many in-play events were recorded against it
and by what (``sync`` is the real API-Football live board; anything else is
the Live tab's hand-entered test events).

    python scripts/inspect_matches.py --leagues "Spanish La Liga" --since 2026-08-01
    python scripts/inspect_matches.py --leagues "Spanish La Liga" --team "Real Madrid"
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select

from app.db.models import EloHistory, LivePrediction, Match, MatchOdds, Prediction, Team
from app.db.session import SessionLocal

SAME_FIXTURE_WINDOW = dt.timedelta(days=1)


def describe(db, m: Match, names: dict[int, str]) -> str:
    live = Counter(
        db.execute(select(LivePrediction.trigger_event).where(LivePrediction.match_id == m.id)).scalars()
    )
    counts = {
        label: db.execute(select(func.count()).select_from(model).where(model.match_id == m.id)).scalar() or 0
        for label, model in (("predictions", Prediction), ("odds", MatchOdds), ("elo rows", EloHistory))
    }
    score = f"{m.home_score}-{m.away_score}" if m.home_score is not None else "no score"
    live_text = ", ".join(f"{k} x{v}" for k, v in live.most_common()) or "none"
    return (
        f"    #{m.id:<7} {m.date:%Y-%m-%d %H:%M}  {names[m.home_team_id]} vs {names[m.away_team_id]}  "
        f"[{score}, {m.status}]\n"
        f"             source={m.source!r}  api_fixture_id={m.api_fixture_id}  "
        f"live_synced_at={m.live_synced_at}  season={m.season!r}\n"
        f"             in-play events: {live_text};  "
        + ", ".join(f"{k}: {v}" for k, v in counts.items())
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--leagues", nargs="+", required=True)
    parser.add_argument("--since", type=dt.date.fromisoformat, default=dt.date.today() - dt.timedelta(days=60))
    parser.add_argument("--team", default=None, help="Only rows involving a club whose name contains this")
    args = parser.parse_args()

    sys.stdout.reconfigure(line_buffering=True)
    since = dt.datetime.combine(args.since, dt.time())

    db = SessionLocal()
    try:
        for league in args.leagues:
            rows = list(
                db.execute(
                    select(Match).where(Match.league == league, Match.date >= since).order_by(Match.date)
                ).scalars()
            )
            ids = {t for m in rows for t in (m.home_team_id, m.away_team_id)}
            names = dict(db.execute(select(Team.id, Team.name).where(Team.id.in_(ids))).all()) if ids else {}
            if args.team:
                needle = args.team.lower()
                rows = [m for m in rows if needle in names[m.home_team_id].lower() or needle in names[m.away_team_id].lower()]

            print(f"\n{'=' * 72}\n{league}: {len(rows)} match(es) since {args.since}")

            by_club_day: dict[tuple[int, dt.date], list[Match]] = defaultdict(list)
            for m in rows:
                for team_id in (m.home_team_id, m.away_team_id):
                    by_club_day[(team_id, m.date.date())].append(m)
            clashes = {k: v for k, v in by_club_day.items() if len(v) > 1}

            by_pair: dict[tuple[int, int], list[Match]] = defaultdict(list)
            for m in rows:
                by_pair[(m.home_team_id, m.away_team_id)].append(m)
            repeats = []
            for group in by_pair.values():
                group.sort(key=lambda m: m.date)
                for a, b in zip(group, group[1:]):
                    if b.date - a.date <= SAME_FIXTURE_WINDOW:
                        repeats.append((a, b))

            flagged: dict[int, Match] = {}
            print(f"\n  Clubs with more than one match on the same day: {len(clashes)}")
            for (team_id, day), matches in sorted(clashes.items(), key=lambda kv: (kv[0][1], names[kv[0][0]])):
                print(f"   {names[team_id]} on {day}: " + ", ".join(f"#{m.id}" for m in matches))
                flagged.update({m.id: m for m in matches})
            print(f"\n  Same fixture stored more than once within a day: {len(repeats)}")
            for a, b in repeats:
                print(f"   #{a.id} and #{b.id}: {names[a.home_team_id]} vs {names[a.away_team_id]}")
                flagged.update({a.id: a, b.id: b})

            if flagged:
                print("\n  Every flagged row:")
                for m in sorted(flagged.values(), key=lambda m: (m.date, m.id)):
                    print(describe(db, m, names))
    finally:
        db.close()


if __name__ == "__main__":
    main()
