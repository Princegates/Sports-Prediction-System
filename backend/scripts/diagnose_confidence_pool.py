#!/usr/bin/env python3
"""Read-only: run the exact pool scripts/generate_random_picks.py and
select_legs_by_confidence draw from (app.betcode.selection.
build_confidence_candidates) against the real database, printing where it
empties -- scheduled matches in the window (every league, unlike
diagnose_betcodes.py's single-league default), how many have a Prediction
row, and for those, the single highest-probability outcome across every
market this project scores, not just the markets build_confidence_candidates
itself is willing to use. Changes nothing.

    python scripts/diagnose_confidence_pool.py --days-ahead 3 --min-probability 0.6
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.betcode.selection import DEFAULT_MARKETS, build_confidence_candidates
from app.db.models import Match, Prediction
from app.db.session import SessionLocal
from app.outcomes.registry import outcomes_from_prediction


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--league", default=None, help="Omit for every league (the pool random picks actually uses).")
    parser.add_argument("--days-ahead", type=int, default=3)
    parser.add_argument("--min-probability", type=float, default=0.6)
    args = parser.parse_args()

    sys.stdout.reconfigure(line_buffering=True)

    db = SessionLocal()
    try:
        now = dt.datetime.utcnow()
        cutoff = now + dt.timedelta(days=args.days_ahead)
        print(f"Window: {now:%Y-%m-%d %H:%M} .. {cutoff:%Y-%m-%d %H:%M} UTC  (league={args.league or 'ANY'})")
        print(f"DEFAULT_MARKETS = {DEFAULT_MARKETS}\n")

        match_query = select(Match).where(Match.date >= now, Match.date < cutoff, Match.status == "SCHEDULED")
        if args.league:
            match_query = match_query.where(Match.league == args.league)
        matches = list(db.execute(match_query).scalars())
        print(f"1. SCHEDULED matches in window (every league): {len(matches)}")
        by_league: dict[str, int] = {}
        for m in matches:
            by_league[m.league] = by_league.get(m.league, 0) + 1
        for league, count in sorted(by_league.items(), key=lambda kv: -kv[1]):
            print(f"     {league!r}: {count}")

        match_ids = [m.id for m in matches]
        if not match_ids:
            print("\nNo scheduled matches at all in this window -- nothing downstream can work.")
            return

        pred_rows = list(db.execute(select(Prediction).where(Prediction.match_id.in_(match_ids))).scalars())
        pred_by_match = {p.match_id: p for p in pred_rows}
        print(f"\n2. Of those, matches WITH a Prediction row: {len(pred_by_match)} / {len(matches)}")

        print(f"\n3. Per match: outcomes_from_prediction() best result, any market vs. DEFAULT_MARKETS only:")
        by_id = {m.id: m for m in matches}
        for mid, prediction in pred_by_match.items():
            m = by_id[mid]
            all_outcomes = outcomes_from_prediction(prediction)
            best_any = max(all_outcomes, key=lambda o: o.probability, default=None)
            default_only = [o for o in all_outcomes if o.market in DEFAULT_MARKETS]
            best_default = max(default_only, key=lambda o: o.probability, default=None)
            dq = prediction.data_quality_score
            print(
                f"   match#{mid} (league={m.league!r}): total_outcomes={len(all_outcomes)} "
                f"data_quality_score={dq!r} "
                f"best_any={(best_any.market, best_any.selection, round(best_any.probability, 3)) if best_any else None} "
                f"best_default_markets={(best_default.market, best_default.selection, round(best_default.probability, 3)) if best_default else None}"
            )

        print(f"\n4. build_confidence_candidates() -- the exact function select_random_legs/select_legs_by_confidence call:")
        candidates = build_confidence_candidates(
            db, min_probability=args.min_probability, leagues=(), league=args.league, days_ahead=args.days_ahead,
        )
        print(f"   Candidates returned: {len(candidates)}")
        for leg in candidates[:15]:
            print(f"     match#{leg.match_id} {leg.home_team} vs {leg.away_team} ({leg.league}): {leg.market} -- {leg.selection} @ {leg.model_probability:.0%}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
