#!/usr/bin/env python3
"""Builds this match week's three "jackpot" accumulators -- 10 to 20 legs
each, pooled across every competition this deployment has data for, each
required to clear 50x combined odds -- and publishes them to the
Dashboard's Admin Picks section alongside the three regular weekly tiers
(scripts/generate_weekly_picks.py) and anything a human admin has promoted
by hand.

This is deliberately a separate script and a separate AdminPick.source
family ("system_weekly_jackpot_1/_2/_3"), not a fourth tier bolted onto
generate_weekly_picks.py's TIERS list: that script's shape is one fixed
leg count searched against a closed odds band; this one is a leg-count
*range* searched against an open-ended floor, and forcing both shapes
through the same loop would make neither one read clearly.

Reuses app.betcode.selection.select_floor_leg_slip, which is to this
script what select_ranged_leg_slip is to the regular weekly tiers: the
"safest legs first, no forced fit" search, just sized for a bigger,
floor-only target instead of a fixed-size band. Each of the three slips
excludes the matches the earlier ones already used (see that function's
own docstring for why) so a week with enough qualifying matches produces
three different slips rather than the same one three times over.

Each slip is one row, identified by AdminPick.source
("system_weekly_jackpot_1"/"_2"/"_3") -- re-running this replaces each
row's legs in place rather than accumulating new ones, same as the
regular weekly tiers. A slip that can't be filled this week (not enough
matches, or not enough odds among what's available) is left as whatever
it was last week rather than cleared -- see generate_weekly_picks.py's own
docstring for why that's the better experience than an empty section.

    python scripts/generate_high_risk_slips.py
    python scripts/generate_high_risk_slips.py --dry-run
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.betcode.selection import SlipCriteria, select_floor_leg_slip
from app.db.migrate import init_db
from app.db.models import AdminPick
from app.db.session import SessionLocal, engine

SLIP_COUNT = 3
MIN_LEGS = 10
MAX_LEGS = 20
TARGET_MIN_ODDS = 50.0

# Wider than the regular weekly tiers' default (7 days): three slips of up
# to 20 non-overlapping legs each can need up to 60 distinct matches in one
# run, and a narrow window starves the later slips before the earlier ones
# even get to 50x.
DAYS_AHEAD = 10

SOURCE_PREFIX = "system_weekly_jackpot_"


def _label(index: int, leg_count: int, combined_odds: float) -> str:
    return f"High Risk Jackpot #{index} -- Weekly {leg_count}-Leg Accumulator ({combined_odds:.0f}+ odds)"


def _note(week_of: dt.date) -> str:
    return f"Auto-generated across every competition for the match week of {week_of.isoformat()}."


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Report what would be written without saving it.")
    parser.add_argument("--slip-count", type=int, default=SLIP_COUNT)
    parser.add_argument("--min-legs", type=int, default=MIN_LEGS)
    parser.add_argument("--max-legs", type=int, default=MAX_LEGS)
    parser.add_argument("--target-odds", type=float, default=TARGET_MIN_ODDS)
    parser.add_argument("--days-ahead", type=int, default=DAYS_AHEAD)
    args = parser.parse_args()

    sys.stdout.reconfigure(line_buffering=True)

    init_db(engine)
    db = SessionLocal()
    try:
        criteria = SlipCriteria(bookmaker="system", target_odds=args.target_odds, days_ahead=args.days_ahead)
        week_of = dt.date.today()
        used_match_ids: set[int] = set()

        for index in range(1, args.slip_count + 1):
            result = select_floor_leg_slip(
                db, criteria, args.min_legs, args.max_legs, args.target_odds,
                exclude_match_ids=frozenset(used_match_ids),
            )
            source = f"{SOURCE_PREFIX}{index}"

            if not result.met_target:
                print(f"[jackpot {index}] skipped -- {result.warnings[0] if result.warnings else 'target not met'}")
                continue

            print(
                f"[jackpot {index}] {len(result.legs)} legs, combined odds {result.combined_odds:.2f}, "
                f"combined probability {result.combined_probability:.1%}"
            )
            for leg in result.legs:
                print(f"    {leg.home_team} vs {leg.away_team} ({leg.league}): "
                      f"{leg.market} -- {leg.selection} @ {leg.decimal_odds}")

            used_match_ids.update(leg.match_id for leg in result.legs)

            if args.dry_run:
                continue

            pick = db.execute(select(AdminPick).where(AdminPick.source == source)).scalar_one_or_none()
            if pick is None:
                pick = AdminPick(source=source)
                db.add(pick)

            pick.legs = [{"match_id": leg.match_id, "market": leg.market, "selection": leg.selection} for leg in result.legs]
            pick.priced = True
            pick.label = _label(index, len(result.legs), result.combined_odds)
            pick.note = _note(week_of)
            # The legs just changed, so any code an admin pasted onto last
            # week's row no longer matches this week's slip.
            pick.booking_code = None
            pick.booking_code_bookmaker = None
            pick.created_by_user_id = None
            pick.expires_at = max(leg.kickoff for leg in result.legs) + dt.timedelta(days=2)

        if args.dry_run:
            db.rollback()
            print("\nDry run -- nothing written.")
        else:
            db.commit()
            print("\nSaved.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
