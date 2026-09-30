#!/usr/bin/env python3
"""Builds COUNT randomly-drawn AI Generation slips per cadence -- no risk
tier, no target odds or price, just a random sample of matches whose
favored outcome clears MIN_PROBABILITY -- and publishes them to the
Dashboard's "Random Picks" sections, alongside the hand-curated Admin Picks
and the weekly Low/Medium/High accumulators.

Two independent cadences, each its own COUNT rows, identified by
AdminPick.source ("system_random_daily_<n>" / "system_random_weekly_<n>",
n = 1..COUNT):

    daily    reshuffled once a day    looks ahead DAILY_DAYS_AHEAD days
    weekly   reshuffled once a week   looks ahead WEEKLY_DAYS_AHEAD days

Reuses app.betcode.selection.select_random_legs end to end: the same
"real prediction, one leg per match, meaningful markets, at least
MIN_PROBABILITY" pool select_legs_by_confidence already searches for AI
Generation, just drawn at a random size (MIN_LEGS-MAX_LEGS) instead of
ranked and capped. This script's own job is running that COUNT times per
cadence and writing each result -- or leaving a cadence's row alone if
there currently aren't enough qualifying matches to draw from, same
"stale but valid beats empty" reasoning as generate_weekly_picks.py.

    python scripts/generate_random_picks.py --which daily
    python scripts/generate_random_picks.py --which weekly
    python scripts/generate_random_picks.py --which both --dry-run
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.betcode.selection import select_random_legs
from app.db.migrate import init_db
from app.db.models import AdminPick
from app.db.session import SessionLocal, engine

COUNT = 5
MIN_LEGS = 10
MAX_LEGS = 15
MIN_PROBABILITY = 0.60

# (cadence, days_ahead, display label) -- see module docstring.
CADENCES: dict[str, tuple[int, str]] = {
    "daily": (3, "Random Daily Pick"),
    "weekly": (10, "Random Weekly Pick"),
}

SOURCE_PREFIX = "system_random_"


def _source(cadence: str, n: int) -> str:
    return f"{SOURCE_PREFIX}{cadence}_{n}"


def _note(cadence: str) -> str:
    return (
        f"Randomly drawn from matches at {MIN_PROBABILITY:.0%}+ model probability, reshuffled {cadence} -- "
        "not ranked by risk level, and not the safest possible combination."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--which", choices=["daily", "weekly", "both"], default="both")
    parser.add_argument("--dry-run", action="store_true", help="Report what would be written without saving it.")
    args = parser.parse_args()

    sys.stdout.reconfigure(line_buffering=True)

    cadences = list(CADENCES) if args.which == "both" else [args.which]

    init_db(engine)
    db = SessionLocal()
    try:
        for cadence in cadences:
            days_ahead, label = CADENCES[cadence]
            print(f"=== {cadence} ({days_ahead} days ahead) ===")

            for n in range(1, COUNT + 1):
                result = select_random_legs(
                    db, min_probability=MIN_PROBABILITY, min_legs=MIN_LEGS, max_legs=MAX_LEGS, days_ahead=days_ahead,
                )
                source = _source(cadence, n)

                if len(result.legs) < 3:
                    print(f"[{n}] skipped -- {result.warnings[0] if result.warnings else 'not enough qualifying matches'}")
                    continue

                print(f"[{n}] {len(result.legs)} legs, combined probability {result.combined_probability:.1%}")
                for leg in result.legs:
                    print(f"    {leg.home_team} vs {leg.away_team} ({leg.league}): {leg.market} -- {leg.selection} @ {leg.model_probability:.0%}")

                if args.dry_run:
                    continue

                pick = db.execute(select(AdminPick).where(AdminPick.source == source)).scalar_one_or_none()
                if pick is None:
                    pick = AdminPick(source=source)
                    db.add(pick)

                pick.legs = [{"match_id": leg.match_id, "market": leg.market, "selection": leg.selection} for leg in result.legs]
                pick.priced = False
                pick.label = f"{label} #{n}"
                pick.note = _note(cadence)
                # The legs just changed, so any code an admin pasted onto the
                # previous draw no longer matches this one.
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
