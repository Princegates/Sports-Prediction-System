#!/usr/bin/env python3
"""Draws a fresh random combo from upcoming, real-priced matches and sets it
as the public teaser pick -- the one showcase combo shown with real
selections on the public, unauthenticated Welcome page (see
AdminPick.public_teaser / routes_admin.randomize_public_teaser_pick, whose
logic this mirrors directly against the database rather than going through
the admin API, since this operational path has no authenticated HTTP access
to the deployed backend).

    python scripts/randomize_public_teaser_pick.py
    python scripts/randomize_public_teaser_pick.py --min-odds 8
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse

from sqlalchemy import delete

from app.betcode.selection import SlipCriteria, select_random_priced_legs
from app.db.migrate import init_db
from app.db.models import AdminPick
from app.db.session import SessionLocal, engine


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--min-odds", type=float, default=5.0, help="Target combined odds (default 5.0).")
    args = parser.parse_args()

    if args.min_odds <= 1.0:
        print("--min-odds must be greater than 1.0.", file=sys.stderr)
        raise SystemExit(1)

    init_db(engine)
    db = SessionLocal()
    try:
        result = select_random_priced_legs(db, SlipCriteria(bookmaker="", target_odds=args.min_odds))
        if not result.met_target:
            detail = result.warnings[0] if result.warnings else "Not enough upcoming priced matches right now."
            print(f"Could not reach {args.min_odds}x: {detail}", file=sys.stderr)
            raise SystemExit(1)

        db.execute(delete(AdminPick).where(AdminPick.public_teaser.is_(True)))

        pick = AdminPick(
            legs=[
                {
                    "match_id": leg.match_id,
                    "market": leg.market,
                    "selection": leg.selection,
                    "probability_at_pick": leg.model_probability,
                    "decimal_odds_at_pick": leg.decimal_odds,
                }
                for leg in result.legs
            ],
            priced=True,
            label="Public teaser pick",
            public_teaser=True,
            expires_at=result.expires_at,
        )
        db.add(pick)
        db.commit()

        print(f"Drew {len(result.legs)} leg(s), combined odds {result.combined_odds:.2f}x:")
        for leg in result.legs:
            print(f"    {leg.league}: {leg.home_team} vs {leg.away_team} -- {leg.market}: {leg.selection} @ {leg.decimal_odds}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
