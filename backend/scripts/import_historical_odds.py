#!/usr/bin/env python3
"""Fetch historical market odds, a few hundred requests at a time.

The point is to answer one question: does the model beat the market? Being
right half the time is worth nothing if a bookmaker said the same thing, and
until that is measured the accuracy figure cannot tell you whether there is a
product here.

Answering it does not need live odds. It needs past odds and past results, and
a free API-Football plan serves seasons 2022-2024 -- which openfootball has
already filled with matches. So the comparison is free; it is only slow.

Slow because odds are paged at roughly ten fixtures each, so a league-season
runs to thirty or forty requests against a hundred-a-day budget. This is
therefore **resumable**: it records the last page completed for each
league-season, stops cleanly when the budget runs out, and continues from
there next time. Run it once a day for a few days.

    python scripts/import_historical_odds.py --dry-run
    python scripts/import_historical_odds.py --seasons 2023 --max-requests 80
    python scripts/import_historical_odds.py --progress
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select

from app import app_settings
from app.data.api_football_ingest import TeamIndex, _parse_kickoff
from app.data.providers.api_football import (
    ID_TO_LEAGUE,
    LEAGUE_IDS,
    ApiFootballClient,
    ApiFootballError,
    QuotaExceeded,
)
from app.db.migrate import init_db
from app.db.models import AppSetting, Match, MatchOdds
from app.db.session import SessionLocal, engine

DEFAULT_LEAGUES = ["English Premier League", "Spanish La Liga", "Italian Serie A",
                   "German Bundesliga", "French Ligue 1"]
# What the free plan serves. Asking outside this wastes a request to be told so.
DEFAULT_SEASONS = [2022, 2023, 2024]

_RESULT_MARKETS = {"match winner", "1x2", "full time result"}
_SELECTIONS = {"home": "Home Win", "draw": "Draw", "away": "Away Win"}

# Progress lives in app_settings so it survives between runs without another
# table, and so it is visible in the admin panel rather than hidden in a file
# on a runner that gets destroyed.
_PROGRESS_KEY = "odds_backfill_progress"


def _load_progress(db) -> dict[str, int]:
    row = db.get(AppSetting, _PROGRESS_KEY)
    if row is None or not row.value:
        return {}
    return {
        part.split("=")[0]: int(part.split("=")[1])
        for part in row.value.split(",")
        if "=" in part
    }


def _save_progress(db, progress: dict[str, int]) -> None:
    encoded = ",".join(f"{k}={v}" for k, v in sorted(progress.items()))
    row = db.get(AppSetting, _PROGRESS_KEY)
    if row is None:
        db.add(AppSetting(key=_PROGRESS_KEY, value=encoded, updated_at=dt.datetime.utcnow()))
    else:
        row.value = encoded
        row.updated_at = dt.datetime.utcnow()
    db.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--leagues", nargs="+", default=DEFAULT_LEAGUES)
    parser.add_argument("--seasons", nargs="+", type=int, default=DEFAULT_SEASONS)
    parser.add_argument("--max-requests", type=int, default=2000,
                        help="Ceiling for this run. Leave headroom under the 100/day budget.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--progress", action="store_true", help="Show what has been fetched and stop")
    parser.add_argument("--reset-progress", action="store_true", help="Start the backfill over")
    args = parser.parse_args()

    init_db(engine)
    db = SessionLocal()
    try:
        progress = _load_progress(db)

        if args.reset_progress:
            _save_progress(db, {})
            print("Progress cleared. The next run starts from the first page.")
            return

        if args.progress:
            stored = db.execute(select(func.count()).select_from(MatchOdds)).scalar() or 0
            matched = db.execute(select(func.count(func.distinct(MatchOdds.match_id)))).scalar() or 0
            print(f"{stored} prices stored, covering {matched} matches.")
            if not progress:
                print("No pages fetched yet.")
            for key in sorted(progress):
                print(f"  {key:<40} last page {progress[key]}")
            return

        unknown = [lg for lg in args.leagues if lg not in LEAGUE_IDS]
        if unknown:
            print(f"Unknown league(s): {', '.join(unknown)}", file=sys.stderr)
            raise SystemExit(2)

        outside = [s for s in args.seasons if s not in DEFAULT_SEASONS]
        if outside:
            print(f"Note: {outside} is outside the free plan's 2022-2024 window.")
            print("      Expect it to be refused; each refusal still costs a request.\n")

        print("Plan")
        print(f"  leagues      : {len(args.leagues)}")
        print(f"  seasons      : {', '.join(map(str, args.seasons))}")
        print(f"  ceiling      : {args.max_requests} requests this run")
        print(f"  resuming     : {len(progress)} league-season(s) already started", flush=True)

        if args.dry_run:
            print("\nDry run -- nothing requested, nothing written.")
            return

        values = app_settings.all_values(db)
        key = str(values.get("api_football_key") or "")
        if not key:
            print("\nNo API-Football key found. Set it in Settings -> Data sources.", file=sys.stderr)
            raise SystemExit(1)

        client = ApiFootballClient(
            key,
            host=str(values.get("api_football_host") or "v3.football.api-sports.io"),
            daily_budget=min(int(values.get("api_football_daily_budget") or 7500), args.max_requests),
            per_minute=int(values.get("api_football_per_minute") or 300),
        )

        index = TeamIndex(db)
        stored_total = 0
        captured_at = dt.datetime.utcnow()

        for season in args.seasons:
            for league in args.leagues:
                league_id = LEAGUE_IDS[league]
                marker = f"{league_id}:{season}"
                page = progress.get(marker, 0) + 1

                matches = {
                    (m.home_team_id, m.away_team_id, m.date): m
                    for m in db.execute(
                        select(Match).where(Match.league == league, Match.season.like(f"%{season}%"))
                    ).scalars()
                }

                while True:
                    try:
                        rows, paging = client.get_page(
                            "odds", {"league": league_id, "season": season, "page": page}
                        )
                    except QuotaExceeded:
                        print(f"\nBudget for this run is spent. Resume tomorrow -- progress is saved.")
                        _save_progress(db, progress)
                        print(f"{stored_total} prices stored this run.")
                        return
                    except ApiFootballError as exc:
                        print(f"  {league} {season}: {exc}", file=sys.stderr)
                        break

                    if not rows:
                        break

                    for row in rows:
                        fixture = row.get("fixture") or {}
                        teams = row.get("teams") or {}
                        home = index.resolve((teams.get("home") or {}).get("name") or "")
                        away = index.resolve((teams.get("away") or {}).get("name") or "")
                        if home is None or away is None:
                            continue
                        match = matches.get((home.id, away.id, _parse_kickoff(fixture.get("date") or "")))
                        if match is None:
                            continue

                        for bookmaker in row.get("bookmakers") or []:
                            for bet in bookmaker.get("bets") or []:
                                if (bet.get("name") or "").strip().lower() not in _RESULT_MARKETS:
                                    continue
                                for value in bet.get("values") or []:
                                    selection = _SELECTIONS.get((value.get("value") or "").strip().lower())
                                    if selection is None:
                                        continue
                                    try:
                                        price = float(value.get("odd"))
                                    except (TypeError, ValueError):
                                        continue
                                    db.add(MatchOdds(
                                        match_id=match.id, captured_at=captured_at,
                                        bookmaker=bookmaker.get("name") or "unknown",
                                        market="Match Result", selection=selection,
                                        decimal_odds=price, source_fixture_id=fixture.get("id"),
                                    ))
                                    stored_total += 1

                    db.commit()
                    progress[marker] = page
                    _save_progress(db, progress)

                    total_pages = int(paging.get("total") or page)
                    print(f"  {league} {season}: page {page}/{total_pages} "
                          f"({stored_total} prices so far, {client.quota.used_this_run} requests)", flush=True)

                    if page >= total_pages:
                        break
                    page += 1

        print(f"\n{'=' * 60}")
        print(f"{stored_total} prices stored. {client.quota.used_this_run} requests used.")
        print("\nWhen enough is collected, measure the edge:")
        print("     python scripts/measure_edge.py")
    finally:
        db.close()


if __name__ == "__main__":
    main()
