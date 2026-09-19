#!/usr/bin/env python3
"""Read-only: print the raw shape of one /odds response.

Spends exactly one request. Written to answer one question directly:
odds capture reports 0 prices stored across every league even after
scoping requests by date -- is the API returning nothing at all for
these fixtures, or is it returning rows whose bet/market names don't
match what _parse_bet() recognises (the "unverified against a live
response" gap the ingest module's own docstring flags)?

    python scripts/probe_odds.py --league-id 39 --season 2026 --date 2026-09-26
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import app_settings
from app.data.providers.api_football import ApiFootballClient
from app.db.session import SessionLocal


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--league-id", type=int, required=True)
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--date", type=lambda s: dt.date.fromisoformat(s), required=True)
    parser.add_argument(
        "--bet-name", default=None,
        help="Print every (bookmaker, value, odd) row for this exact bet name, across all fixtures -- "
             "for confirming one market's real value shape when the truncated first-fixture dump doesn't reach it.",
    )
    args = parser.parse_args()

    sys.stdout.reconfigure(line_buffering=True)

    db = SessionLocal()
    try:
        values = app_settings.all_values(db)
        key = str(values.get("api_football_key") or "")
        if not key:
            print("No API-Football key saved.", file=sys.stderr)
            raise SystemExit(1)

        client = ApiFootballClient(
            key,
            host=str(values.get("api_football_host") or "v3.football.api-sports.io"),
            daily_budget=int(values.get("api_football_daily_budget") or 7500),
            per_minute=int(values.get("api_football_per_minute") or 300),
        )

        rows = client.odds(league_id=args.league_id, season=args.season, date=args.date)
        print(f"{len(rows)} fixture(s) with odds returned for league={args.league_id} season={args.season} date={args.date}")

        if not rows:
            print("\nThe API returned nothing for this exact query. Trying without `date`, page 1:")
            rows2 = client.odds(league_id=args.league_id, season=args.season)
            print(f"  {len(rows2)} fixture(s) returned (league+season only, page 1)")
            if rows2:
                print("\nFirst fixture's raw shape:")
                print(json.dumps(rows2[0], indent=2)[:4000])
            return

        first = rows[0]
        print("\nFirst fixture's raw shape:")
        print(json.dumps(first, indent=2)[:6000])

        print("\nBet/market names actually present, across all returned fixtures:")
        seen = set()
        for row in rows:
            for bookmaker in row.get("bookmakers") or []:
                for bet in bookmaker.get("bets") or []:
                    seen.add((bookmaker.get("name"), bet.get("name")))
        for bookmaker_name, bet_name in sorted(seen):
            print(f"  bookmaker={bookmaker_name!r:<20} bet={bet_name!r}")

        if args.bet_name:
            print(f"\nValues for bet={args.bet_name!r}, across all fixtures/bookmakers:")
            for row in rows:
                for bookmaker in row.get("bookmakers") or []:
                    for bet in bookmaker.get("bets") or []:
                        if bet.get("name") == args.bet_name:
                            values = [(v.get("value"), v.get("odd")) for v in bet.get("values") or []]
                            print(f"  bookmaker={bookmaker.get('name')!r:<20} values={values}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
