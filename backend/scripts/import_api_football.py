#!/usr/bin/env python3
"""Import European fixtures and market odds from API-Football.

Reads the key from the admin settings panel, falling back to
API_FOOTBALL_KEY in the environment -- so once it is saved in the panel, this
needs only DATABASE_URL and can run from CI with no secret of its own.

    python scripts/import_api_football.py --dry-run
    python scripts/import_api_football.py --leagues "UEFA Champions League"
    python scripts/import_api_football.py --leagues "UEFA Champions League" --odds

The budget is a hundred requests a day, so this refuses to start without
knowing what it will cost. ``--dry-run`` prints the plan and spends nothing;
``--max-requests`` is a hard ceiling that stops the run rather than the API
stopping it.

Fixtures resolve to clubs already in the database. A tie whose clubs cannot be
matched is skipped and listed, never attached to a guess -- one wrong club
turns a prediction into confident nonsense.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import app_settings
from app.data.api_football_ingest import import_fixtures, import_odds
from app.data.providers.api_football import (
    LEAGUE_IDS,
    ApiFootballClient,
    ApiFootballError,
    QuotaExceeded,
)
from app.db.migrate import init_db
from app.db.session import SessionLocal, engine

DEFAULT_LEAGUES = ["UEFA Champions League"]


def current_season(today: dt.date | None = None) -> int:
    today = today or dt.date.today()
    return today.year if today.month >= 7 else today.year - 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--leagues", nargs="+", default=DEFAULT_LEAGUES,
                        help=f"Known: {', '.join(sorted(LEAGUE_IDS))}")
    parser.add_argument("--season", type=int, default=None, help="Defaults to the current season")
    parser.add_argument("--odds", action="store_true", help="Also capture three-way market prices")
    parser.add_argument("--days-ahead", type=int, default=14, help="Fixture window for odds capture")
    parser.add_argument("--max-requests", type=int, default=20,
                        help="Hard ceiling for this run. The daily budget is 100.")
    parser.add_argument("--dry-run", action="store_true", help="Print the plan and spend nothing")
    args = parser.parse_args()

    season = args.season or current_season()

    unknown = [lg for lg in args.leagues if lg not in LEAGUE_IDS]
    if unknown:
        print(f"Unknown league(s): {', '.join(unknown)}", file=sys.stderr)
        print(f"Known: {', '.join(sorted(LEAGUE_IDS))}", file=sys.stderr)
        raise SystemExit(2)

    planned = len(args.leagues) * (2 if args.odds else 1)
    print("Plan")
    print(f"  leagues      : {', '.join(args.leagues)}")
    print(f"  season       : {season}")
    print(f"  odds         : {'yes' if args.odds else 'no'}")
    print(f"  requests     : about {planned} (ceiling {args.max_requests}, daily budget 100)")

    if planned > args.max_requests:
        print(f"\nThat exceeds --max-requests={args.max_requests}. Raise it deliberately or narrow the run.",
              file=sys.stderr)
        raise SystemExit(2)

    if args.dry_run:
        print("\nDry run -- nothing was requested and nothing was written.")
        return

    init_db(engine)
    db = SessionLocal()
    try:
        values = app_settings.all_values(db)
        key = str(values.get("api_football_key") or "")
        if not key:
            print("\nNo API-Football key found.", file=sys.stderr)
            print("  Set it in the admin panel (Settings -> Data sources), or as", file=sys.stderr)
            print("  API_FOOTBALL_KEY in the environment.", file=sys.stderr)
            raise SystemExit(1)

        client = ApiFootballClient(
            key,
            host=str(values.get("api_football_host") or "v3.football.api-sports.io"),
            daily_budget=min(int(values.get("api_football_daily_budget") or 100), args.max_requests),
        )

        total_inserted = total_updated = 0
        unresolved: list[str] = []

        for league in args.leagues:
            league_id = LEAGUE_IDS[league]
            print(f"\n=== {league} ===")

            try:
                report = import_fixtures(db, client, league_id=league_id, season=season)
            except QuotaExceeded as exc:
                print(f"  stopped: {exc}", file=sys.stderr)
                break
            except ApiFootballError as exc:
                print(f"  failed: {exc}", file=sys.stderr)
                continue

            total_inserted += report.inserted
            total_updated += report.updated
            unresolved.extend(report.skipped_unresolved)
            print(f"  fixtures: {report.considered} seen, {report.inserted} new, {report.updated} updated")
            if report.skipped_unresolved:
                print(f"            {len(report.skipped_unresolved)} skipped (clubs not recognised)")

            if args.odds:
                try:
                    odds_report = import_odds(db, client, league_id=league_id, season=season)
                    print(f"  odds    : {odds_report.inserted} prices stored")
                except QuotaExceeded as exc:
                    print(f"  odds stopped: {exc}", file=sys.stderr)
                    break
                except ApiFootballError as exc:
                    print(f"  odds failed: {exc}", file=sys.stderr)

        print(f"\n{'=' * 60}")
        print(f"{total_inserted} fixtures added, {total_updated} updated.")
        print(f"Requests used this run: {client.quota.used_this_run}")
        if client.quota.remaining_reported is not None:
            print(f"The API reports {client.quota.remaining_reported} left today.")

        if unresolved:
            print(f"\n{len(unresolved)} fixture(s) skipped because a club could not be matched:")
            for line in unresolved[:15]:
                print(f"  {line}")
            if len(unresolved) > 15:
                print(f"  ... and {len(unresolved) - 15} more")
            print("\nAdd the club's provider spelling to EXPLICIT_ALIASES in")
            print("app/data/team_matching.py and re-run -- skipping is deliberate, since")
            print("attaching a tie to the wrong club would corrupt its Elo history.")

        if total_inserted:
            print("\nGenerate predictions for the new fixtures:")
            print(f'     python scripts/generate_predictions.py --league "{args.leagues[0]}"')
    finally:
        db.close()


if __name__ == "__main__":
    main()
