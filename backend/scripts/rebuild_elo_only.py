#!/usr/bin/env python3
"""Rebuild Elo history for a league without the rest of scripts/backtest.py.

For CL_RESOLVER_LEAGUES (app/data/providers/api_football.py): the ~45
domestic leagues added only so a Champions League club whose home league
this project doesn't otherwise track still has a real Team row and Elo
history to resolve a tie against (see api_football.py's own comment on that
list, and app/data/api_football_ingest.py's module docstring on why an
unresolved club is skipped rather than guessed).

scripts/backtest.py does far more than Elo: gradient-boosting training and a
full train/validation/test backtest, each reading every match in every
league -- expensive enough that it previously burned a whole month's managed-
Postgres transfer allowance in two days when run nightly (see
.github/workflows/refresh-predictions.yml's own comment on that incident).
These 45 leagues exist purely to feed Elo and Poisson (which needs no
training step at all -- it reads Match rows live) for the occasional
Champions League tie; a full ML model and backtest per league is cost this
feature doesn't need and this project has already been burned by spending
without asking. This script does only the one thing that's actually needed:
replay results into EloHistory, league by league, with no feature
engineering, no model artifact, no held-out evaluation.

    python scripts/rebuild_elo_only.py                     # every CL_RESOLVER_LEAGUES entry
    python scripts/rebuild_elo_only.py --leagues "Scottish Premiership" "Belgian Pro League"
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse

from sqlalchemy import select

from app.db.migrate import init_db
from app.db.models import Match
from app.db.session import SessionLocal, engine
from app.prediction_models import elo


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--leagues", nargs="+", default=None,
                        help="Defaults to every league in CL_RESOLVER_LEAGUES")
    parser.add_argument("--min-matches", type=int, default=20,
                        help="Skip a league with fewer finished matches than this -- not enough for a "
                             "meaningful Elo replay, and some of these leagues are thin on API-Football")
    args = parser.parse_args()

    sys.stdout.reconfigure(line_buffering=True)

    if args.leagues is None:
        from app.data.providers.api_football import CL_RESOLVER_LEAGUES
        leagues = list(CL_RESOLVER_LEAGUES)
    else:
        leagues = args.leagues

    init_db(engine)
    db = SessionLocal()
    rebuilt = skipped = 0
    try:
        for league in leagues:
            count = db.execute(
                select(Match.id).where(Match.league == league, Match.home_score.is_not(None))
            ).scalars().all()
            if len(count) < args.min_matches:
                print(f"  {league}: only {len(count)} finished match(es) -- skipping")
                skipped += 1
                continue

            ratings = elo.rebuild_elo_history(db, league)
            print(f"  {league}: {len(count)} match(es) replayed, {len(ratings)} team(s) rated")
            rebuilt += 1

        db.commit()
        print(f"\n{rebuilt} league(s) rebuilt, {skipped} skipped (too little data).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
