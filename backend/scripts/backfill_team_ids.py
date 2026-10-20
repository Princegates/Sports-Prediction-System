"""Learn API-Football's team id for every club, one request per league.

Team crests (and id-first club matching) need each club's API-Football id.
Clubs pick one up on their own whenever a live sync or a fixture import
sees them, but that can take weeks for a club that rarely plays a tracked
live match -- this fills them all in at once.

    python scripts/backfill_team_ids.py --dry-run
    python scripts/backfill_team_ids.py
    python scripts/backfill_team_ids.py --leagues "English Premier League" --season 2026

Domestic leagues only (a European competition's clubs are the domestic
rows already covered). Never creates clubs and never overwrites an id a
club already has -- see app.data.api_football_ingest.backfill_team_ids.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import app_settings
from app.data.api_football_ingest import backfill_team_ids
from app.data.providers.api_football import LEAGUE_IDS, ApiFootballClient, ApiFootballError, QuotaExceeded
from app.db.migrate import init_db
from app.db.session import SessionLocal, engine
from app.prediction_models.elo import EUROPEAN_COMPETITIONS

DOMESTIC_LEAGUES = [name for name in LEAGUE_IDS if name not in EUROPEAN_COMPETITIONS]


def current_season(today: dt.date | None = None) -> int:
    # Same rule as import_api_football.py: a season is named for the year
    # it starts, and European seasons start in July/August.
    today = today or dt.date.today()
    return today.year if today.month >= 7 else today.year - 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--leagues", nargs="+", default=DOMESTIC_LEAGUES, choices=DOMESTIC_LEAGUES, metavar="LEAGUE")
    parser.add_argument("--season", type=int, default=None, help="Default: the current season")
    parser.add_argument("--max-requests", type=int, default=100, help="Hard ceiling for this run")
    parser.add_argument("--dry-run", action="store_true", help="Print the plan and spend nothing")
    args = parser.parse_args()

    season = args.season or current_season()
    print(f"{len(args.leagues)} league(s), season {season}: {len(args.leagues)} request(s)")
    if args.dry_run:
        for league in args.leagues:
            print(f"  would fetch: {league}")
        return

    init_db(engine)
    db = SessionLocal()
    try:
        values = app_settings.all_values(db)
        key = str(values.get("api_football_key") or "")
        if not key:
            print("No API-Football key saved (Settings -> Data sources) -- nothing to do.", file=sys.stderr)
            raise SystemExit(1)
        client = ApiFootballClient(
            key,
            host=str(values.get("api_football_host") or "v3.football.api-sports.io"),
            daily_budget=min(int(values.get("api_football_daily_budget") or 7500), args.max_requests),
            per_minute=int(values.get("api_football_per_minute") or 300),
        )

        learned = known = 0
        failed: list[str] = []
        for league in args.leagues:
            try:
                report = backfill_team_ids(db, client, league_id=LEAGUE_IDS[league], season=season)
            except QuotaExceeded as exc:
                print(f"stopped: {exc}", file=sys.stderr)
                break
            except ApiFootballError as exc:
                print(f"{league}: failed -- {exc}", file=sys.stderr)
                failed.append(league)
                continue
            db.commit()
            learned += report.learned
            known += report.already_known
            line = f"{league}: {report.learned} learned, {report.already_known} already known"
            if report.unresolved:
                line += f", {len(report.unresolved)} unmatched ({', '.join(report.unresolved[:5])}{'...' if len(report.unresolved) > 5 else ''})"
            print(line)

        print(f"\nDone: {learned} club id(s) learned, {known} already known, {client.quota.used_this_run} request(s) used.")
        if failed:
            raise SystemExit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
