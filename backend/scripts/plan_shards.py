#!/usr/bin/env python3
"""Splits a league list into N shards balanced by finished-match count, for
the weekly retrain's matrixed training jobs (see .github/workflows/
refresh-predictions.yml) -- an even split by *count of leagues* would load
one shard with England/Spain/Italy/Germany's decades of backfilled history
and leave the rest nearly idle, which defeats the point of sharding.

Prints one compact JSON line: an array of N arrays of league names. A
league this run would skip anyway -- too little data, or a European
competition scripts/backtest.py never replays as its own league -- is left
out of every shard rather than padding one with dead weight.

    python scripts/plan_shards.py --leagues "English Premier League" ... --shards 6
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select

from app.db.models import Match
from app.db.session import SessionLocal
from app.prediction_models.elo import EUROPEAN_COMPETITIONS

# Mirrors scripts/backtest.py's own floor (run_all_leagues / _discover_leagues)
# -- no point reserving a shard slot for a league backtest.py will just skip.
MIN_FINISHED_MATCHES = 100


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--leagues", nargs="+", required=True)
    parser.add_argument("--shards", type=int, default=6)
    args = parser.parse_args()

    db = SessionLocal()
    try:
        counts: dict[str, int] = {}
        for league in args.leagues:
            if league in EUROPEAN_COMPETITIONS:
                continue
            n = db.execute(
                select(func.count()).select_from(Match).where(Match.league == league, Match.home_score.is_not(None))
            ).scalar()
            if n >= MIN_FINISHED_MATCHES:
                counts[league] = n
    finally:
        db.close()

    # Longest-processing-time-first: the biggest league goes to whichever
    # shard currently has the least total work. A classic bin-packing
    # heuristic, not an exact balance -- it doesn't need to be, since a
    # shard's real cost isn't purely linear in match count anyway, just
    # close enough that no one shard is left carrying most of the run.
    shards: list[list[str]] = [[] for _ in range(args.shards)]
    totals = [0] * args.shards
    for league, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        i = totals.index(min(totals))
        shards[i].append(league)
        totals[i] += n

    print(json.dumps(shards, separators=(",", ":")))


if __name__ == "__main__":
    main()
