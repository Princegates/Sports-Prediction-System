#!/usr/bin/env python3
"""Builds this match week's three system accumulators -- Low, Medium and
High risk, each exactly 10 legs, pooled across every competition this
deployment has data for -- and publishes them to the Dashboard's Admin
Picks section alongside anything a human admin has promoted by hand.

Combined-odds bands (inclusive), fixed by product spec:

    low     5  - 10
    medium  11 - 20
    high    21 - 30

Reuses app.betcode.selection's existing machinery end to end: the same
"real prices only, one leg per match, meaningful markets by default" rules
build_candidate_legs already enforces for the AI Generation page, and
select_ranged_leg_slip's sliding-window search for a fixed leg count inside
a target range. This script's own job is just running that three times
with a different band, and writing the result -- or not, if the target
band can't be reached this week (see select_ranged_leg_slip's docstring
for why a near-but-outside-the-band result is never published as if it
were inside it).

Each tier is one row, identified by AdminPick.source
("system_weekly_low"/"_medium"/"_high") -- re-running this replaces that
row's legs in place rather than accumulating a new one per run, so it is
safe to run every week (or re-run by hand) without manual cleanup. A tier
that can't be filled this week is left as whatever it was last week rather
than being cleared -- a stale-but-valid slip from a few days ago is a
better user experience than an empty section, and it self-expires the
moment its own legs' matches kick off regardless.

    python scripts/generate_weekly_picks.py
    python scripts/generate_weekly_picks.py --dry-run
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.betcode.selection import SlipCriteria, select_ranged_leg_slip
from app.db.migrate import init_db
from app.db.models import AdminPick
from app.db.session import SessionLocal, engine

LEG_COUNT = 10

# (tier, min combined odds, max combined odds) -- see module docstring.
TIERS: list[tuple[str, float, float]] = [
    ("low", 5.0, 10.0),
    ("medium", 11.0, 20.0),
    ("high", 21.0, 30.0),
]

SOURCE_PREFIX = "system_weekly_"


def _label(tier: str, target_min: float, target_max: float) -> str:
    return f"{tier.capitalize()} Risk -- Weekly {LEG_COUNT}-Leg Accumulator ({target_min:.0f}-{target_max:.0f} odds)"


def _note(week_of: dt.date) -> str:
    return f"Auto-generated across every competition for the match week of {week_of.isoformat()}."


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Report what would be written without saving it.")
    args = parser.parse_args()

    sys.stdout.reconfigure(line_buffering=True)

    init_db(engine)
    db = SessionLocal()
    try:
        criteria = SlipCriteria(bookmaker="system", target_odds=max(t[2] for t in TIERS))
        week_of = dt.date.today()

        for tier, target_min, target_max in TIERS:
            result = select_ranged_leg_slip(db, criteria, LEG_COUNT, target_min, target_max)
            source = f"{SOURCE_PREFIX}{tier}"

            if not result.met_target:
                print(f"[{tier}] skipped -- {result.warnings[0] if result.warnings else 'target not met'}")
                continue

            print(
                f"[{tier}] {len(result.legs)} legs, combined odds {result.combined_odds:.2f}, "
                f"combined probability {result.combined_probability:.1%}"
            )
            for leg in result.legs:
                print(f"    {leg.home_team} vs {leg.away_team} ({leg.league}): "
                      f"{leg.market} -- {leg.selection} @ {leg.decimal_odds}")

            if args.dry_run:
                continue

            pick = db.execute(select(AdminPick).where(AdminPick.source == source)).scalar_one_or_none()
            if pick is None:
                pick = AdminPick(source=source)
                db.add(pick)

            pick.legs = [{"match_id": leg.match_id, "market": leg.market, "selection": leg.selection} for leg in result.legs]
            pick.priced = True
            pick.label = _label(tier, target_min, target_max)
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
