#!/usr/bin/env python3
"""Separate two real clubs that were wrongly merged into one row, using
API-Football's fixtures to decide which stored match was whose. See
app/data/club_split.py.

Dry run by default: prints what would move and writes nothing. One
API-Football request per competition per season.

    python scripts/split_merged_club.py --league "Italian Serie A" --merged-name "AC Milan" \\
        --restore-name "Inter" --restore-aliases "FC Internazionale Milano" "Internazionale" \\
        --competitions "Italian Serie A" "UEFA Champions League" "UEFA Europa League" \\
        --seasons 2021 2022 2023 2024 2025 2026
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import app_settings
from app.data.club_split import split_club
from app.data.providers.api_football import LEAGUE_IDS, ApiFootballClient
from app.db.migrate import init_db
from app.db.session import SessionLocal, engine


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--league", required=True, help="The league both clubs play in")
    parser.add_argument("--merged-name", required=True, help="The surviving row's current name")
    parser.add_argument("--restore-name", required=True, help="Name for the club being separated out")
    parser.add_argument("--restore-aliases", nargs="*", default=[])
    parser.add_argument("--competitions", nargs="+", required=True)
    parser.add_argument("--seasons", nargs="+", type=int, required=True)
    parser.add_argument("--apply", action="store_true", help="Write the changes. Without this, nothing changes.")
    args = parser.parse_args()

    sys.stdout.reconfigure(line_buffering=True)
    unknown = [c for c in args.competitions if c not in LEAGUE_IDS]
    if unknown:
        raise SystemExit(f"Unknown competition(s): {', '.join(unknown)}")

    init_db(engine)
    db = SessionLocal()
    try:
        values = app_settings.all_values(db)
        key = str(values.get("api_football_key") or "")
        if not key:
            raise SystemExit("No API-Football key saved in Settings -> Data sources.")
        client = ApiFootballClient(
            key,
            host=str(values.get("api_football_host") or "v3.football.api-sports.io"),
            daily_budget=min(int(values.get("api_football_daily_budget") or 7500), 100),
            per_minute=int(values.get("api_football_per_minute") or 300),
        )

        report = split_club(
            db, client,
            league=args.league, merged_name=args.merged_name,
            restore_name=args.restore_name, restore_aliases=args.restore_aliases,
            competitions=args.competitions, seasons=args.seasons, apply=args.apply,
        )

        verb = "Handed back" if args.apply else "Would hand back"
        print(f"\n{args.restore_name}: {'new row' if report.created else 'existing row'} #{report.restored_team_id}")
        print(f"{verb} {report.reassigned} match(es) from {args.merged_name!r} to {args.restore_name!r}:")
        for competition, count in report.per_competition.items():
            print(f"  {competition:<28} {count}")
        print(f"Already right: {report.already_right}")
        print(f"Provider fixtures with no stored match: {len(report.unmatched)}")
        for line in report.unmatched[:15]:
            print(f"  {line}")
        if report.ambiguous:
            print(f"Left alone, more than one stored row fits: {len(report.ambiguous)}")
            for line in report.ambiguous:
                print(f"  {line}")
        print(f"API-Football requests used: {report.requests_used}")
        if not args.apply:
            print("\nDry run -- re-run with --apply to write these changes.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
