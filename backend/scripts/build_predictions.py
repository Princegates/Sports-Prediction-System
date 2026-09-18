#!/usr/bin/env python3
"""Pull upcoming fixtures (free TheSportsDB test key) and generate + store
predictions for each one.

Example:
    python scripts/build_predictions.py --league "English Premier League"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.data.ingest import import_upcoming_fixtures
from app.db.models import Match
from app.db.migrate import init_db
from app.db.session import SessionLocal, engine
from app.prediction_models.ml_model import LeagueFeatureCache
from app.prediction_service import build_prediction_for_match
from sqlalchemy import select


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--league", required=True, help='Human-readable league name, e.g. "English Premier League"')
    args = parser.parse_args()

    settings = get_settings()
    init_db(engine)
    db = SessionLocal()

    try:
        inserted = import_upcoming_fixtures(db, args.league, api_key=settings.thesportsdb_api_key)
        print(f"Imported {inserted} new upcoming fixtures for {args.league}.")

        scheduled = list(
            db.execute(
                select(Match).where(Match.league == args.league, Match.status == "SCHEDULED")
            ).scalars()
        )
        print(f"Generating predictions for {len(scheduled)} scheduled matches ...")

        # One cache for the run -- see FeatureCachePool. Per match, this
        # reloads the league's whole history for every fixture.
        feature_cache = LeagueFeatureCache(db, args.league) if scheduled else None

        for match in scheduled:
            prediction = build_prediction_for_match(db, match, feature_cache=feature_cache)
            print(
                f"  {match.home_team.name} vs {match.away_team.name} ({match.date:%Y-%m-%d %H:%M}) "
                f"-> {prediction.global_outcome_selection} {prediction.global_outcome_probability:.1%} "
                f"[{prediction.confidence}]"
            )
    finally:
        db.close()


if __name__ == "__main__":
    main()
