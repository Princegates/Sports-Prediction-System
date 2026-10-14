#!/usr/bin/env python3
"""Read-only: how many Champions League matches have a Prediction row, and
spot-checks a few previously-unresolvable clubs' Elo ratings.

One-off verification for the 45-league Champions League backfill.

    python scripts/check_cl_resolution.py
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select

from app.db.models import Match, Prediction, Team
from app.db.session import SessionLocal
from app.prediction_models.elo import get_rating_before

SPOT_CHECK_CLUBS = ["Celtic", "Club Brugge KV", "Dinamo Zagreb", "Olympiakos Piraeus", "Sabah FA"]


def main() -> None:
    db = SessionLocal()
    try:
        total = db.execute(select(func.count()).select_from(Match).where(Match.league == "UEFA Champions League")).scalar()
        upcoming = db.execute(
            select(func.count()).select_from(Match).where(
                Match.league == "UEFA Champions League", Match.status == "SCHEDULED"
            )
        ).scalar()
        predicted = db.execute(
            select(func.count(func.distinct(Prediction.match_id)))
            .select_from(Prediction)
            .join(Match, Match.id == Prediction.match_id)
            .where(Match.league == "UEFA Champions League")
        ).scalar()
        upcoming_predicted = db.execute(
            select(func.count(func.distinct(Prediction.match_id)))
            .select_from(Prediction)
            .join(Match, Match.id == Prediction.match_id)
            .where(Match.league == "UEFA Champions League", Match.status == "SCHEDULED")
        ).scalar()

        print(f"UEFA Champions League: {total} match(es) stored, {upcoming} upcoming (SCHEDULED).")
        print(f"  {predicted} match(es) have a Prediction row ({upcoming_predicted} of the {upcoming} upcoming ones).")

        print("\nSpot-checking a few previously-unresolvable clubs:")
        for name in SPOT_CHECK_CLUBS:
            team = db.execute(select(Team).where(Team.name == name)).scalar_one_or_none()
            if team is None:
                print(f"  {name}: no Team row found")
                continue
            rating = get_rating_before(db, team.id, dt.datetime.utcnow(), 1500.0)
            print(f"  {name} ({team.league}): Elo {rating:.0f}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
