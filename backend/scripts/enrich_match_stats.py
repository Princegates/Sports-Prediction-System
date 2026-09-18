#!/usr/bin/env python3
"""Backfill shots, cards and referees onto matches already in the database.

Fixtures and scores come from openfootball; what actually happened during a
match -- shots, shots on target, corners, fouls, cards, referee -- comes from
football-data.co.uk. This reconciles the two by date and team name and
attaches the statistics to existing rows. It never inserts a match.

Why bother: a scoreline is a small, noisy sample of a match. Shot counts
describe how it was played, and a side that keeps out-shooting opponents
while losing is usually about to stop losing. That is signal the model
currently cannot see.

    python scripts/enrich_match_stats.py                       # five big leagues, recent seasons
    python scripts/enrich_match_stats.py --leagues E0 SP1 --seasons 2324 2425
    python scripts/enrich_match_stats.py --show-unmatched      # list names that failed to resolve

**Read the match rate it prints.** Anything below ~95% usually means a club
whose short name isn't recognised; run with --show-unmatched and add it to
EXPLICIT_ALIASES in app/data/team_matching.py. The importer deliberately
skips what it cannot resolve confidently rather than guessing, because
attaching one club's statistics to another would quietly corrupt training.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.data.ingest import enrich_match_stats
from app.data.providers.football_data_co_uk import LEAGUE_CODES
from app.db.migrate import init_db
from app.db.session import SessionLocal, engine

# football-data.co.uk's own 4-digit season codes, matching the openfootball
# seasons the importer pulls by default.
DEFAULT_SEASONS = ["2122", "2223", "2324", "2425", "2526", "2627"]
DEFAULT_LEAGUES = ["E0", "SP1", "I1", "D1", "F1"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--leagues", nargs="+", default=DEFAULT_LEAGUES,
                        help=f"football-data.co.uk codes. Known: {', '.join(sorted(LEAGUE_CODES))}")
    parser.add_argument("--seasons", nargs="+", default=DEFAULT_SEASONS)
    parser.add_argument("--show-unmatched", action="store_true",
                        help="Print every fixture that could not be resolved")
    args = parser.parse_args()

    init_db(engine)

    db = SessionLocal()
    total_rows = total_matched = total_updated = 0
    all_unmatched: list[str] = []

    try:
        for league_code in args.leagues:
            league_name = LEAGUE_CODES.get(league_code, league_code)
            print(f"\n=== {league_name} ({league_code}) ===", flush=True)

            for season in args.seasons:
                try:
                    report = enrich_match_stats(db, league_code, season)
                except Exception as exc:  # noqa: BLE001 -- one bad season shouldn't stop the rest
                    print(f"  {season}: failed ({type(exc).__name__}: {exc})", file=sys.stderr)
                    continue

                if report.csv_rows == 0:
                    print(f"  {season}: no data upstream")
                    continue

                total_rows += report.csv_rows
                total_matched += report.matched
                total_updated += report.updated
                all_unmatched.extend(f"[{league_name} {season}] {u}" for u in report.unmatched)

                flag = "" if report.match_rate >= 0.95 else "   <-- low, see --show-unmatched"
                print(
                    f"  {season}: {report.matched}/{report.csv_rows} matched "
                    f"({report.match_rate:.0%}), {report.updated} updated{flag}"
                )
    finally:
        db.close()

    rate = total_matched / total_rows if total_rows else 0.0
    print(f"\n{'=' * 60}")
    print(f"Matched {total_matched}/{total_rows} CSV rows ({rate:.1%}); {total_updated} matches enriched.")

    if all_unmatched:
        print(f"{len(all_unmatched)} rows could not be resolved.")
        if args.show_unmatched:
            for row in all_unmatched:
                print(f"  {row}")
        else:
            print("Re-run with --show-unmatched to list them.")
        print(
            "\nA recurring club name here means an abbreviation the matcher doesn't know.\n"
            "Add it to EXPLICIT_ALIASES in app/data/team_matching.py and re-run."
        )

    if total_updated:
        print("\nRetrain so the models can use the new statistics:")
        print("     python scripts/backtest.py --league-name \"English Premier League\"")


if __name__ == "__main__":
    main()
