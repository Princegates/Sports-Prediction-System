#!/usr/bin/env python3
"""Read-only: for every Champions League club this project currently skips
(no domestic Team row to resolve to -- see app/data/api_football_ingest.py's
module docstring), look up which domestic league it actually plays in and
that league's API-Football id.

One-off reconnaissance for deciding which new domestic leagues to add to
LEAGUE_IDS in app/data/providers/api_football.py, so Champions League ties
involving those clubs can resolve to a real Team row instead of being
skipped. Writes nothing -- it only reads fixtures and league data.

    python scripts/map_cl_unresolved_leagues.py
    python scripts/map_cl_unresolved_leagues.py --season 2026
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import app_settings
from app.data.api_football_ingest import TeamIndex
from app.data.providers.api_football import ApiFootballClient, ApiFootballError, QuotaExceeded
from app.db.migrate import init_db
from app.db.session import SessionLocal, engine
from scripts.import_api_football import current_season


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--league-id", type=int, default=2, help="API-Football league id (2 = Champions League)")
    parser.add_argument("--season", type=int, default=None)
    args = parser.parse_args()

    sys.stdout.reconfigure(line_buffering=True)
    season = args.season or current_season()

    init_db(engine)
    db = SessionLocal()
    try:
        values = app_settings.all_values(db)
        key = str(values.get("api_football_key") or "")
        if not key:
            print("No API-Football key found in settings.", file=sys.stderr)
            raise SystemExit(1)

        client = ApiFootballClient(
            key,
            host=str(values.get("api_football_host") or "v3.football.api-sports.io"),
            daily_budget=int(values.get("api_football_daily_budget") or 7500),
            per_minute=int(values.get("api_football_per_minute") or 300),
        )

        index = TeamIndex(db)
        rows = client.fixtures(league_id=args.league_id, season=season)
        print(f"{len(rows)} fixture(s) fetched for league {args.league_id}, season {season}.")

        # (name, api_football_team_id) for every club with no domestic Team
        # row, deduplicated.
        unresolved: dict[int, str] = {}
        for row in rows:
            teams = row.get("teams") or {}
            for side in ("home", "away"):
                team = teams.get(side) or {}
                name, team_id = team.get("name"), team.get("id")
                if not name or team_id is None:
                    continue
                if index.resolve(name) is None:
                    unresolved[team_id] = name

        print(f"{len(unresolved)} unresolved club(s). Looking up each one's domestic league...\n")

        # club name -> (league name, league id, country)
        found: dict[str, tuple[str, int, str]] = {}
        no_league: list[str] = []
        failed: list[str] = []

        for team_id, name in sorted(unresolved.items(), key=lambda kv: kv[1]):
            try:
                entries = client.get("leagues", {"team": team_id, "current": "true"})
            except (ApiFootballError, QuotaExceeded) as exc:
                print(f"  {name}: lookup failed ({exc})", file=sys.stderr)
                failed.append(name)
                continue

            # A club's current leagues include its domestic top flight and
            # any cups/continental competitions -- only "League" type, and
            # never the continental one itself, is the answer this needs.
            domestic = [
                e for e in entries
                if (e.get("league") or {}).get("type") == "League"
                and (e.get("country") or {}).get("name") not in (None, "World")
            ]
            if not domestic:
                no_league.append(name)
                continue

            league = domestic[0]["league"]
            country = (domestic[0].get("country") or {}).get("name") or "?"
            found[name] = (league.get("name") or "?", league.get("id") or 0, country)

        print(f"\n{'=' * 70}")
        print(f"{len(found)} club(s) resolved to a domestic league:\n")

        by_league: dict[tuple[str, int, str], list[str]] = {}
        for name, key3 in found.items():
            by_league.setdefault(key3, []).append(name)

        for (league_name, league_id, country), clubs in sorted(by_league.items(), key=lambda kv: (kv[0][2], kv[0][0])):
            print(f"  {country} -- {league_name} (API-Football id {league_id})")
            for club in sorted(clubs):
                print(f"      {club}")

        if no_league:
            print(f"\n{len(no_league)} club(s) with no current domestic league found (youth/reserve side, or API gap):")
            for name in no_league:
                print(f"    {name}")

        if failed:
            print(f"\n{len(failed)} lookup(s) failed:")
            for name in failed:
                print(f"    {name}")

        print(f"\nRequests used: {client.quota.used_this_run}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
