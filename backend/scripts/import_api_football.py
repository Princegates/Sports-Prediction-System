#!/usr/bin/env python3
"""Import European fixtures and market odds from API-Football.

Reads the key from the admin settings panel, falling back to
API_FOOTBALL_KEY in the environment -- so once it is saved in the panel, this
needs only DATABASE_URL and can run from CI with no secret of its own.

    python scripts/import_api_football.py --dry-run
    python scripts/import_api_football.py --leagues "UEFA Champions League"
    python scripts/import_api_football.py --leagues "UEFA Champions League" --odds

The daily budget is finite and shared, so this refuses to start without
knowing what it will cost. ``--dry-run`` prints the plan and spends nothing;
``--max-requests`` is a hard ceiling that stops the run rather than the API
stopping it.

A run that asked for leagues it could not import exits non-zero. Importing
nothing is a failure even when every error was caught, and a green tick over
an empty import is worse than a red one.

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
from app.data.api_football_ingest import UnresolvedClub, import_fixtures, import_odds
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


# Above this, a rejected candidate is close enough that the two names are
# probably the same club spelled differently. Below it, the club plays in a
# league this project does not hold and no alias will ever help.
LIKELY_SPELLING = 0.60


def report_unresolved(clubs: dict[str, UnresolvedClub], fixtures: int) -> None:
    """Print the skipped clubs, separating the two reasons for skipping.

    The count alone is useless -- 186 skipped fixtures in a Champions League
    season is either completely normal (qualifying rounds full of clubs from
    leagues this project does not hold) or a spelling bug hiding half the
    league phase, and the number is identical either way. So the nearest
    stored club and its score are printed, and the plausible spellings are
    listed first and never truncated: those are the only ones a human can act
    on.
    """

    ranked = sorted(clubs.values(), key=lambda c: (-c.score, -c.fixtures, c.name))
    spellings = [c for c in ranked if c.score >= LIKELY_SPELLING]
    absent = [c for c in ranked if c.score < LIKELY_SPELLING]

    def line(club: UnresolvedClub) -> str:
        nearest = f"closest: {club.nearest} ({club.score:.2f})" if club.nearest else "no clubs stored"
        plural = "fixture" if club.fixtures == 1 else "fixtures"
        return f"    {club.name:<34} {club.fixtures:>3} {plural:<8}  {nearest}"

    print(f"\n{fixtures} fixture(s) skipped: {len(clubs)} club(s) could not be matched.")

    if spellings:
        print(f"\n  Close to a club already stored -- probably the same club, spelled")
        print(f"  differently, and worth an alias:")
        for club in spellings:
            print(line(club))
        print("\n  Add the provider's spelling to EXPLICIT_ALIASES in")
        print("  app/data/team_matching.py and re-run.")

    if absent:
        print(f"\n  Nothing close in the database -- these play in leagues this project")
        print(f"  does not hold, so skipping them is correct:")
        for club in absent[:10]:
            print(line(club))
        if len(absent) > 10:
            print(f"    ... and {len(absent) - 10} more")

    if not spellings:
        print("\n  No plausible spelling matches, so nothing here needs an alias.")

    print("\nSkipping is deliberate: a tie attached to the wrong club would corrupt")
    print("that club's Elo history while looking perfectly normal.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--leagues", nargs="+", default=DEFAULT_LEAGUES,
                        help=f"Known: {', '.join(sorted(LEAGUE_IDS))}")
    parser.add_argument("--season", type=int, default=None, help="Defaults to the current season")
    parser.add_argument("--odds", action="store_true", help="Also capture three-way market prices")
    parser.add_argument("--days-ahead", type=int, default=14, help="Fixture window for odds capture")
    parser.add_argument("--max-requests", type=int, default=200,
                        help="Hard ceiling for this run, under the plan's daily budget.")
    parser.add_argument("--dry-run", action="store_true", help="Print the plan and spend nothing")
    args = parser.parse_args()

    # stdout is a pipe under CI and therefore block-buffered, while stderr is
    # not: without this the plan appears *below* the failure it came before.
    sys.stdout.reconfigure(line_buffering=True)

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
    print(f"  requests     : about {planned} (ceiling {args.max_requests})")

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
            # "Not saved" and "saved in a different database" produce the same
            # empty value, and they need opposite fixes, so say which.
            from sqlalchemy import func, select

            from app.config import get_settings
            from app.db.models import AppSetting

            overrides = db.execute(select(func.count()).select_from(AppSetting)).scalar() or 0
            saved_keys = sorted(db.execute(select(AppSetting.key)).scalars())
            where = get_settings().normalized_database_url.split("@")[-1]

            print("\nNo API-Football key found.", file=sys.stderr)
            print(f"  database          : {where}", file=sys.stderr)
            print(f"  settings saved    : {overrides}", file=sys.stderr)
            if saved_keys:
                print(f"  keys present      : {', '.join(saved_keys)}", file=sys.stderr)

            if overrides == 0:
                print(
                    "\n  This database has no saved settings at all, so it is probably not the\n"
                    "  one the admin panel writes to. Check that DATABASE_URL here matches the\n"
                    "  one set on the API service.",
                    file=sys.stderr,
                )
            elif "api_football_key" not in saved_keys:
                print(
                    "\n  Other settings are here, so this is the right database and the key\n"
                    "  simply was not saved. In the panel, type it into Settings -> Data\n"
                    "  sources -> API-Football key and press Save; the button must change from\n"
                    "  'No changes' to 'Save 1 change' before it will store anything.",
                    file=sys.stderr,
                )
            else:
                print("\n  The key row exists but is empty -- re-enter it and save.", file=sys.stderr)
            raise SystemExit(1)

        daily_budget = int(values.get("api_football_daily_budget") or 7500)
        client = ApiFootballClient(
            key,
            host=str(values.get("api_football_host") or "v3.football.api-sports.io"),
            daily_budget=min(daily_budget, args.max_requests),
            per_minute=int(values.get("api_football_per_minute") or 300),
        )
        print(f"  budget       : {daily_budget}/day, "
              f"{int(values.get('api_football_per_minute') or 300)}/minute (from settings)")

        total_inserted = total_updated = 0
        unresolved: dict[str, UnresolvedClub] = {}
        skipped_fixtures = 0
        failed: list[str] = []
        stopped_early = False

        for league in args.leagues:
            league_id = LEAGUE_IDS[league]
            print(f"\n=== {league} ===")

            try:
                report = import_fixtures(db, client, league_id=league_id, season=season)
            except QuotaExceeded as exc:
                print(f"  stopped: {exc}", file=sys.stderr)
                stopped_early = True
                break
            except ApiFootballError as exc:
                print(f"  failed: {exc}", file=sys.stderr)
                failed.append(league)
                continue

            total_inserted += report.inserted
            total_updated += report.updated
            skipped_fixtures += len(report.skipped_unresolved)
            for name, club in report.unresolved_clubs.items():
                seen = unresolved.get(name)
                if seen is None:
                    unresolved[name] = club
                else:
                    seen.fixtures += club.fixtures
            print(f"  fixtures: {report.considered} seen, {report.inserted} new, {report.updated} updated")
            if report.skipped_unresolved:
                print(f"            {len(report.skipped_unresolved)} skipped (clubs not recognised)")

            if args.odds:
                try:
                    odds_report = import_odds(db, client, league_id=league_id, season=season)
                    print(f"  odds    : {odds_report.inserted} prices stored")
                except QuotaExceeded as exc:
                    print(f"  odds stopped: {exc}", file=sys.stderr)
                    stopped_early = True
                    break
                except ApiFootballError as exc:
                    print(f"  odds failed: {exc}", file=sys.stderr)
                    failed.append(f"{league} (odds)")

        print(f"\n{'=' * 60}")
        print(f"{total_inserted} fixtures added, {total_updated} updated.")
        print(f"Requests used this run: {client.quota.used_this_run}")
        if client.quota.remaining_reported is not None:
            print(f"The API reports {client.quota.remaining_reported} left today.")

        if unresolved:
            report_unresolved(unresolved, skipped_fixtures)

        if total_inserted:
            print("\nGenerate predictions for the new fixtures:")
            print(f'     python scripts/generate_predictions.py --league "{args.leagues[0]}"')

        # A run that imported nothing because every request was refused is a
        # failed run. Catching the error kept the other leagues going; it must
        # not also turn the job green.
        if failed:
            print(f"\nDid not import: {', '.join(failed)} -- see the errors above.", file=sys.stderr)
            raise SystemExit(1)
        if stopped_early:
            print("\nStopped at the request ceiling before finishing. Raise --max-requests "
                  "or re-run to continue.", file=sys.stderr)
            raise SystemExit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
