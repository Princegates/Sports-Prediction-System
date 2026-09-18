#!/usr/bin/env python3
"""Ask the API what it will actually serve for a competition.

Answers "do I have access to X?" with data rather than with a catalogue
listing, because those are different questions and the difference has already
cost this project a wrong answer: /leagues cheerfully lists every season a
competition has ever had, including ones the plan refuses to return.

So this does both. It finds the competition's id, prints what the catalogue
claims -- including whether odds are covered, which decides whether edge
measurement is possible at all -- and then asks for real fixtures. Only the
last step proves anything.

    python scripts/check_league_access.py --search "Europa League" --country Turkey
    python scripts/check_league_access.py --country Ghana

The key comes from the settings panel, so this needs only DATABASE_URL. Two
requests per competition, and it says what it spent.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import app_settings
from app.data.providers.api_football import ApiFootballClient, ApiFootballError, QuotaExceeded
from app.db.session import SessionLocal


def current_season(today: dt.date | None = None) -> int:
    today = today or dt.date.today()
    return today.year if today.month >= 7 else today.year - 1


def describe(entry: dict, season: int) -> dict:
    league = entry.get("league") or {}
    country = entry.get("country") or {}
    seasons = entry.get("seasons") or []

    this_season = next((s for s in seasons if s.get("year") == season), None)
    coverage = (this_season or {}).get("coverage") or {}
    years = [s.get("year") for s in seasons if isinstance(s.get("year"), int)]

    return {
        "id": league.get("id"),
        "name": league.get("name") or "?",
        "type": league.get("type") or "?",
        "country": country.get("name") or "?",
        "listed": this_season is not None,
        "odds": bool(coverage.get("odds")),
        "stats": bool((coverage.get("fixtures") or {}).get("statistics_fixtures")),
        "first": min(years) if years else None,
        "last": max(years) if years else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--search", nargs="*", default=[], help="Competition names to look up")
    parser.add_argument("--country", nargs="*", default=[], help="Countries whose competitions to list")
    parser.add_argument("--season", type=int, default=None, help="Defaults to the current season")
    parser.add_argument("--max-probe", type=int, default=8,
                        help="How many competitions to ask real fixtures for")
    args = parser.parse_args()

    sys.stdout.reconfigure(line_buffering=True)

    if not args.search and not args.country:
        parser.error("give at least one --search term or --country")

    season = args.season or current_season()

    db = SessionLocal()
    try:
        values = app_settings.all_values(db)
        key = str(values.get("api_football_key") or "")
        if not key:
            print("No API-Football key in the settings panel (Settings -> Data sources).", file=sys.stderr)
            raise SystemExit(1)

        client = ApiFootballClient(
            key,
            host=str(values.get("api_football_host") or "v3.football.api-sports.io"),
            daily_budget=int(values.get("api_football_daily_budget") or 7500),
            per_minute=int(values.get("api_football_per_minute") or 300),
        )

        found: dict[int, dict] = {}
        for term in args.search:
            for entry in client.get("leagues", {"search": term}):
                info = describe(entry, season)
                if info["id"] is not None:
                    found.setdefault(info["id"], info)
        for country in args.country:
            for entry in client.get("leagues", {"country": country}):
                info = describe(entry, season)
                if info["id"] is not None:
                    found.setdefault(info["id"], info)

        if not found:
            print("Nothing matched. Check the spelling, or search a broader term.")
            return

        # Leagues before cups, then the ones whose current season is listed.
        ranked = sorted(
            found.values(),
            key=lambda i: (i["type"] != "League", not i["listed"], i["name"]),
        )

        print(f"=== What the catalogue claims (season {season}) ===\n")
        print(f"  {'id':>5}  {'competition':<34} {'country':<14} {'type':<7} {'season':<7} odds  stats  history")
        for info in ranked:
            listed = "listed" if info["listed"] else "absent"
            history = f"{info['first']}-{info['last']}" if info["first"] else "?"
            print(f"  {info['id']:>5}  {info['name'][:34]:<34} {info['country'][:14]:<14} "
                  f"{info['type'][:7]:<7} {listed:<7} {'yes' if info['odds'] else 'no':<5} "
                  f"{'yes' if info['stats'] else 'no':<6} {history}")

        probes = [i for i in ranked if i["listed"]][: args.max_probe]
        if not probes:
            print(f"\nNo competition here lists season {season}, so there is nothing to ask for.")
            return

        print(f"\n=== What it actually serves ===\n")
        print("  The catalogue lists a competition's whole history, not the seasons this")
        print("  plan may read. Only this part is evidence.\n")

        for info in probes:
            try:
                rows = client.fixtures(league_id=info["id"], season=season)
            except QuotaExceeded as exc:
                print(f"  {info['name']}: stopped -- {exc}")
                break
            except ApiFootballError as exc:
                print(f"  {info['name']:<34} REFUSED   {exc}")
                continue

            if rows:
                print(f"  {info['name']:<34} readable  {len(rows)} fixtures, "
                      f"odds {'covered' if info['odds'] else 'not covered'}")
            else:
                print(f"  {info['name']:<34} empty     no fixtures returned for {season}")

        print(f"\nRequests used: {client.quota.used_this_run}")
        if client.quota.remaining_reported is not None:
            print(f"The API reports {client.quota.remaining_reported} left today.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
