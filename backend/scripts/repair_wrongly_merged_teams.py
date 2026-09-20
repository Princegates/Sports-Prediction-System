#!/usr/bin/env python3
"""Repairs five real clubs a September 19 run of merge_duplicate_teams.py
wrongly fused into a rival or same-city club's row -- before this project's
team-name matching fix (app.data.team_matching's _CITY_ONLY_TOKENS /
_FALSE_COGNATE_PAIRS), "AC Milan" and "Internazionale Milano" both reduced
to a shared "Milan"/"Milano" token, and "Real Madrid", "Atletico Madrid",
"Rayo Vallecano" and "Barcelona"/"Espanyol" all reduced to a shared city
name, so the merge script saw two different clubs as one.

Three-step repair, safe to re-run:

1. Drop the victim's exact name from the survivor's aliases. The merge that
   caused this recorded the deleted victim's name there (see
   merge_duplicate_teams.py's own merge step), and TeamResolver checks exact
   aliases *before* the suffix-stripped key -- left in place, re-importing
   the victim's real fixtures below would resolve "AC Milan" straight back
   to the survivor and silently recreate the exact collision this script
   exists to undo.

2. Re-import each victim club's real historical results from openfootball,
   the same source the original fixtures came from. With its shadowing
   alias gone, TeamResolver creates a fresh, correct Team row for the victim
   (its old one was deleted by the bad merge) and inserts a fresh Match row
   for each of its real games -- the *existing*, wrongly-attributed row for
   that same real fixture is untouched at this step, since it's keyed by
   the *survivor's* team id, not the fresh one just created here.

3. Sweep every one of the survivor's current matches and delete the ones
   that are now exact duplicates of a freshly re-imported row for the same
   real fixture (same league, season, date, score, and the *other* team
   unchanged) -- these are exactly the ones that belonged to the victim all
   along. A match where the survivor is recorded as playing itself
   (home_team_id == away_team_id, an artifact only the wrong merge could
   produce) is one of these too, deleted once the real derby's own
   two-sided row exists from step 2. A survivor match with no matching
   fresh row (a cup tie or friendly openfootball doesn't carry, or simply
   one of the survivor's own genuine, correctly-attributed fixtures, which
   never gets a duplicate created for it) is left alone -- same "refuse
   rather than delete real data on a guess" rule this project applies
   everywhere else.

   Before a wrong match itself is deleted, every row this project keys off
   Match.id for it (predictions, live predictions, odds snapshots, Elo
   history, match views, featured picks -- MATCH_DEPENDENTS) is deleted
   too, and any chat message's context_match_id pointing at it is cleared.
   Postgres refuses the match deletion outright otherwise (matches.id is a
   live foreign key for every one of them), and none of that data would be
   valid to keep anyway -- it was computed against the wrong team.

Dry run by default. import_openfootball_season commits internally, so a
real dry run needs Session.commit swapped for a flush for the duration of
this script -- otherwise "dry run" would still write the re-imported rows
for real, whatever --delete said. Pass --delete to write for real (a real
commit, and the wrongly-attributed matches actually deleted).

Elo history is not hand-repaired here -- run scripts/backtest.py for the
affected leagues afterward, which rebuilds it from scratch by replaying
matches in date order, so it never needs to agree with a team-id
renumbering.

    python scripts/repair_wrongly_merged_teams.py
    python scripts/repair_wrongly_merged_teams.py --delete
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, select, update

from app.data.ingest import import_openfootball_season
from app.db.migrate import init_db
from app.db.models import ChatMessage, EloHistory, FeaturedPick, LivePrediction, Match, MatchOdds, MatchView, Prediction, Team
from app.db.session import SessionLocal, engine

# Every row keyed off a Match this project stores, other than the Match
# itself. Each one was computed from -- or points at -- a wrongly-attributed
# fixture, so it is deleted right along with it rather than kept as an
# orphan (which Postgres refuses outright: matches.id is a live foreign key
# for every one of these) or reattributed -- a prediction or odds snapshot
# computed against the wrong team is not valid data for any other match
# either. Same table list repair_rescheduled_duplicates.py already uses for
# the same reason.
MATCH_DEPENDENTS = [Prediction, LivePrediction, MatchOdds, EloHistory, MatchView, FeaturedPick]

# openfootball's own season-folder naming; matches every season this
# project has ever imported (see scripts/bootstrap.py's DEFAULT_SEASONS).
SEASONS = ["2021-22", "2022-23", "2023-24", "2024-25", "2025-26", "2026-27"]

# (league, survivor's current name, victim club that was merged into it) --
# see this module's docstring for why each pair collided. Names are the
# exact spellings openfootball itself uses, and the exact strings
# merge_duplicate_teams.py recorded as aliases on the survivor.
BAD_MERGES = [
    ("Italian Serie A", "FC Internazionale Milano", "AC Milan"),
    ("Spanish La Liga", "Real Madrid CF", "Club Atlético de Madrid"),
    ("Spanish La Liga", "Real Madrid CF", "Rayo Vallecano de Madrid"),
    ("Spanish La Liga", "FC Barcelona", "RCD Espanyol de Barcelona"),
    ("French Ligue 1", "Paris Saint-Germain", "Paris FC"),
]


def _strip_shadowing_alias(db, league: str, survivor_name: str, victim_name: str) -> None:
    survivor = db.execute(select(Team).where(Team.name == survivor_name, Team.league == league)).scalar_one_or_none()
    if survivor is not None and victim_name in (survivor.aliases or []):
        survivor.aliases = [a for a in survivor.aliases if a != victim_name]
        db.flush()


def _reimport_league(db, league: str) -> None:
    for season in SEASONS:
        try:
            result = import_openfootball_season(db, league, season)
        except Exception as exc:  # network hiccup or a season not yet published upstream
            print(f"    [{season}] skipped: {exc}")
            continue
        if result["inserted"] or result["updated"]:
            print(f"    [{season}] inserted {result['inserted']}, updated {result['updated']}")


def _survivor_fingerprint_plan(db, league: str, survivor: Team) -> tuple[list[Match], list[Match]]:
    """Every match still on ``survivor`` that step 2 just made an exact,
    correctly-attributed duplicate of -- and separately, every one left
    genuinely unresolved (no fresh match to compare against, including the
    survivor's own real, already-correct fixtures). Never deletes a
    survivor match with no confirmed replacement."""

    survivor_matches = list(
        db.execute(
            select(Match).where(
                Match.league == league,
                (Match.home_team_id == survivor.id) | (Match.away_team_id == survivor.id),
            )
        ).scalars()
    )

    all_matches = list(db.execute(select(Match).where(Match.league == league)).scalars())
    by_fingerprint: dict[tuple, list[Match]] = {}
    for m in all_matches:
        key = (m.season, m.date.date(), m.home_score, m.away_score)
        by_fingerprint.setdefault(key, []).append(m)

    wrong: list[Match] = []
    unresolved: list[Match] = []

    for m in survivor_matches:
        key = (m.season, m.date.date(), m.home_score, m.away_score)
        siblings = [s for s in by_fingerprint.get(key, []) if s.id != m.id]

        if m.home_team_id == survivor.id and m.away_team_id == survivor.id:
            # A team can never really play itself -- only the bad merge
            # could have produced this. Confirm step 2 recreated the real,
            # two-sided derby before deleting the artifact.
            if any(s.home_team_id != s.away_team_id for s in siblings):
                wrong.append(m)
            else:
                unresolved.append(m)
            continue

        replaced = False
        for s in siblings:
            if m.home_team_id == survivor.id and s.away_team_id == m.away_team_id and s.home_team_id != survivor.id:
                wrong.append(m)
                replaced = True
                break
            if m.away_team_id == survivor.id and s.home_team_id == m.home_team_id and s.away_team_id != survivor.id:
                wrong.append(m)
                replaced = True
                break
        if not replaced:
            unresolved.append(m)

    return wrong, unresolved


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--delete", action="store_true", help="Actually write. Without this, nothing changes.")
    args = parser.parse_args()

    sys.stdout.reconfigure(line_buffering=True)

    init_db(engine)
    db = SessionLocal()
    try:
        if not args.delete:
            # import_openfootball_season commits internally -- without this,
            # every "dry run" would still write the re-imported rows for
            # real. Swapping commit for flush keeps everything visible to
            # this session (so the report below reflects what a real run
            # would find) while leaving nothing for the final rollback to
            # discard but itself.
            db.commit = db.flush

        leagues = sorted({league for league, _survivor, _victim in BAD_MERGES})

        print("Step 1: removing the victim names merge_duplicate_teams.py left as survivor aliases")
        for league, survivor_name, victim_name in BAD_MERGES:
            _strip_shadowing_alias(db, league, survivor_name, victim_name)

        print("\nStep 2: re-importing openfootball history for the affected leagues")
        for league in leagues:
            print(f"  {league}")
            _reimport_league(db, league)

        print("\nStep 3: sweeping each survivor for now-duplicate wrong matches")
        # Grouped by (league, survivor) rather than iterated straight off
        # BAD_MERGES: Real Madrid CF absorbed two victims (Atletico and
        # Rayo), and _survivor_fingerprint_plan depends only on the
        # survivor, not which victim -- iterating BAD_MERGES directly would
        # run the identical sweep twice for it and double-count its matches
        # into total_wrong.
        survivors: dict[tuple[str, str], list[str]] = {}
        for league, survivor_name, victim_name in BAD_MERGES:
            survivors.setdefault((league, survivor_name), []).append(victim_name)

        total_wrong = 0
        for (league, survivor_name), victim_names in survivors.items():
            survivor = db.execute(
                select(Team).where(Team.name == survivor_name, Team.league == league)
            ).scalar_one_or_none()
            if survivor is None:
                print(f"  [{league}] survivor {survivor_name!r} not found -- skipping")
                continue

            wrong, unresolved = _survivor_fingerprint_plan(db, league, survivor)
            total_wrong += len(wrong)
            print(f"\n  [{league}] {survivor_name!r} (id {survivor.id}): "
                  f"{len(wrong)} match(es) belong to {' / '.join(map(repr, victim_names))}, "
                  f"{len(unresolved)} left alone (no confirmed fresh replacement -- includes the "
                  "survivor's own real fixtures)")
            for m in wrong[:10]:
                print(f"    #{m.id}  {m.season}  {m.date.date()}  {m.home_score}-{m.away_score}  "
                      f"(home_team_id={m.home_team_id}, away_team_id={m.away_team_id})")
            if len(wrong) > 10:
                print(f"    ... and {len(wrong) - 10} more")

            if args.delete:
                wrong_ids = [m.id for m in wrong]
                if wrong_ids:
                    for model in MATCH_DEPENDENTS:
                        db.execute(delete(model).where(model.match_id.in_(wrong_ids)))
                    db.execute(
                        update(ChatMessage)
                        .where(ChatMessage.context_match_id.in_(wrong_ids))
                        .values(context_match_id=None)
                    )
                for m in wrong:
                    db.delete(m)

        if args.delete:
            db.commit()
            print(f"\nDeleted {total_wrong} wrongly-attributed match(es) across {len(leagues)} league(s).")
            quoted_leagues = " ".join(f'"{league}"' for league in leagues)
            print("\nNext step: retrain, which rebuilds Elo history from scratch and refreshes")
            print('predictions -- run the "Refresh predictions" workflow with mode=retrain,')
            print(f"leagues={quoted_leagues}")
            print(f"  (or locally: python scripts/bootstrap.py --skip-data --leagues {quoted_leagues})")
        else:
            db.rollback()
            print(f"\nDry run -- nothing written. {total_wrong} match(es) would be deleted, and fresh, "
                  "correct rows created for each victim club above. Re-run with --delete to apply.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
