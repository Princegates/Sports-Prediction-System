#!/usr/bin/env python3
"""Generate + store predictions for all SCHEDULED matches already in the
database for a league (use this after fetch_openfootball_data.py, which
inserts real unplayed fixtures directly -- no separate fixture-fetch step
needed).

Example:
    python scripts/generate_predictions.py --league "English Premier League" --days-ahead 10
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.db.models import Base, Match
from app.db.session import SessionLocal, engine
from app.prediction_service import build_prediction_for_match


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--league", required=True)
    parser.add_argument("--days-ahead", type=int, default=14)
    args = parser.parse_args()

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        now = dt.datetime.utcnow()
        cutoff = now + dt.timedelta(days=args.days_ahead)
        scheduled = list(
            db.execute(
                select(Match).where(
                    Match.league == args.league,
                    Match.status == "SCHEDULED",
                    Match.date >= now,
                    Match.date < cutoff,
                ).order_by(Match.date.asc())
            ).scalars()
        )
        print(f"Generating predictions for {len(scheduled)} scheduled matches in the next {args.days_ahead} days ...")
        for match in scheduled:
            prediction = build_prediction_for_match(db, match)
            print(
                f"  {match.date:%Y-%m-%d %H:%M}  {match.home_team.name} vs {match.away_team.name}"
                f"  -> {prediction.global_outcome_selection} {prediction.global_outcome_probability:.1%}"
                f"  [{prediction.confidence}]  (1X2: {prediction.home_win:.0%}/{prediction.draw:.0%}/{prediction.away_win:.0%})"
            )
    finally:
        db.close()


if __name__ == "__main__":
    main()
