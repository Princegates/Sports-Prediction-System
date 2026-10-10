#!/usr/bin/env python3
"""Finds every already-settled pick whose frozen result disagrees with what
grade_outcome says about its match(es) right now. Read-only unless --apply.

Settlement is intentionally one-way (see app/pick_settlement.py's own
docstring): once a pick leaves "pending" nothing re-grades it automatically.
That's correct once a result is genuinely final -- but Match.is_finished
used to mean only "home_score and away_score are both set", which the
live-sync job made true within minutes of kickoff (it writes the current
running score on every poll of a match that's still on, status "LIVE",
so the Live tab has something to show). A pick settled during that window
got graded off whatever the score happened to be at that moment, then
frozen wrong forever -- fixed in app/db/models.py (Match.is_finished now
also requires status == "FINISHED"), but the fix doesn't reach back and
re-grade anything it already froze before it landed.

This recomputes every settled leg's grade_outcome verdict fresh against
each match's current row -- exactly what settle_admin_pick/
settle_booking_slip/settle_featured_pick would compute if the pick were
still pending and got settled right now -- aggregates those fresh leg
verdicts the same way _overall_result does, and flags a row where that
disagrees with what's actually frozen on it.

One subtlety this has to get right: a combo correctly freezes "lost" the
moment ANY ONE leg is confirmed lost, even while other legs are still
unplayed (see _overall_result) -- that is not a bug, and comparing fresh
*aggregated* results rather than leg-by-leg is what keeps this script from
flagging it as one. What it does catch: a leg that was stored "lost" (or
"won") because it was graded off a live, in-progress score now regrading
to "pending" (its match genuinely isn't finished, even now) or to a
different verdict (its match finished, but not the way the premature grade
assumed) -- either way the fresh aggregate stops matching what's frozen.

Covers AdminPick (combo slips), BookingSlip (member-generated codes), and
FeaturedPick (single-outcome Guda Picks) -- every table pick_settlement.py
settles.

--apply resets a flagged row back to "pending" (clearing result/
leg_results/settled_at) so the very next ordinary read -- the same
settle_admin_pick/settle_booking_slip/settle_featured_pick call every
listing endpoint already makes -- re-settles it for real, off whatever the
match's row actually says now. It does not invent or write a result
itself; a leg on an ungradeable market still lands on "unresolved" exactly
as it would for a pick settling for the first time.

    python scripts/audit_settled_picks.py
    python scripts/audit_settled_picks.py --team Lens
    python scripts/audit_settled_picks.py --team Lens --apply
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AdminPick, BookingSlip, FeaturedPick, Match, Team
from app.db.session import SessionLocal
from app.outcomes.grading import grade_outcome
from app.pick_settlement import RESULT_LOST, RESULT_PENDING, RESULT_UNRESOLVED, RESULT_WON, _overall_result


def _team_names(db: Session, match_ids: set[int]) -> dict[int, tuple[str, str]]:
    if not match_ids:
        return {}
    matches = db.execute(select(Match).where(Match.id.in_(match_ids))).scalars().all()
    team_ids = {t for m in matches for t in (m.home_team_id, m.away_team_id)}
    names = dict(db.execute(select(Team.id, Team.name).where(Team.id.in_(team_ids))).all())
    return {m.id: (names.get(m.home_team_id, "?"), names.get(m.away_team_id, "?")) for m in matches}


def _regrade_leg(match: Match | None, market: str, selection: str) -> tuple[str, str]:
    """("won"|"lost"|"unresolved"|"pending", why) regraded fresh, same rule
    pick_settlement._grade_legs uses -- kept in sync by construction since
    this calls the same grade_outcome, not a reimplementation of it."""

    if match is None:
        return RESULT_PENDING, "match no longer exists"
    if not match.is_finished:
        return RESULT_PENDING, f"status={match.status!r} home_score={match.home_score!r} away_score={match.away_score!r}"
    verdict = grade_outcome(
        market, selection,
        home_score=match.home_score, away_score=match.away_score,
        ht_home_score=match.ht_home_score, ht_away_score=match.ht_away_score,
    )
    if verdict is True:
        return RESULT_WON, f"final {match.home_score}-{match.away_score}"
    if verdict is False:
        return RESULT_LOST, f"final {match.home_score}-{match.away_score}"
    return RESULT_UNRESOLVED, f"market not auto-gradeable (final {match.home_score}-{match.away_score})"


def _matches_team(name_pair: tuple[str, str] | None, needle: str | None) -> bool:
    if needle is None:
        return True
    if name_pair is None:
        return False
    needle = needle.lower()
    return needle in name_pair[0].lower() or needle in name_pair[1].lower()


def _audit_combo(db: Session, table_label: str, rows: list, *, needle: str | None) -> list[dict]:
    match_ids = {leg["match_id"] for row in rows for leg in row.legs}
    names = _team_names(db, match_ids)
    matches = {m.id: m for m in db.execute(select(Match).where(Match.id.in_(match_ids))).scalars()}

    flagged = []
    for row in rows:
        leg_fixtures = [names.get(leg["match_id"]) for leg in row.legs]
        if needle is not None and not any(_matches_team(nm, needle) for nm in leg_fixtures):
            continue

        fresh_leg_results = []
        for leg in row.legs:
            match = matches.get(leg["match_id"])
            fresh, why = _regrade_leg(match, leg["market"], leg["selection"])
            fresh_leg_results.append((fresh, why, leg, names.get(leg["match_id"])))
        fresh_overall = _overall_result([r[0] for r in fresh_leg_results])

        if fresh_overall == row.result:
            continue

        flagged.append({
            "table": table_label, "id": row.id, "stored_result": row.result,
            "fresh_result": fresh_overall, "settled_at": row.settled_at,
            "legs": fresh_leg_results, "row": row,
        })
    return flagged


def _audit_featured(db: Session, rows: list[FeaturedPick], *, needle: str | None) -> list[dict]:
    match_ids = {row.match_id for row in rows}
    names = _team_names(db, match_ids)
    matches = {m.id: m for m in db.execute(select(Match).where(Match.id.in_(match_ids))).scalars()}

    flagged = []
    for row in rows:
        name_pair = names.get(row.match_id)
        if needle is not None and not _matches_team(name_pair, needle):
            continue
        match = matches.get(row.match_id)
        fresh, why = _regrade_leg(match, row.market, row.selection)
        if fresh == row.result:
            continue

        flagged.append({
            "table": "FeaturedPick", "id": row.id, "stored_result": row.result, "fresh_result": fresh,
            "settled_at": row.settled_at,
            "legs": [(fresh, why, {"match_id": row.match_id, "market": row.market, "selection": row.selection}, name_pair)],
            "row": row,
        })
    return flagged


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--team", default=None, help="Only picks with a leg involving a club whose name contains this")
    parser.add_argument("--apply", action="store_true", help="Reset every flagged row to pending. Without this, report only.")
    args = parser.parse_args()

    sys.stdout.reconfigure(line_buffering=True)

    db = SessionLocal()
    try:
        admin_picks = list(db.execute(select(AdminPick).where(AdminPick.result != RESULT_PENDING)).scalars())
        booking_slips = list(db.execute(select(BookingSlip).where(BookingSlip.result != RESULT_PENDING)).scalars())
        featured_picks = list(db.execute(select(FeaturedPick).where(FeaturedPick.result != RESULT_PENDING)).scalars())
        print(f"Checking {len(admin_picks)} settled AdminPick, {len(booking_slips)} settled BookingSlip, "
              f"{len(featured_picks)} settled FeaturedPick row(s)"
              + (f" with a leg matching {args.team!r}" if args.team else "") + "\n")

        flagged = (
            _audit_combo(db, "AdminPick", admin_picks, needle=args.team)
            + _audit_combo(db, "BookingSlip", booking_slips, needle=args.team)
            + _audit_featured(db, featured_picks, needle=args.team)
        )

        if not flagged:
            print("No mismatches found.")
            return

        print(f"Flagged {len(flagged)} row(s):\n")
        for f in flagged:
            print(f"=== {f['table']} id={f['id']}  stored={f['stored_result']!r} "
                  f"fresh={f['fresh_result']!r}  settled_at={f['settled_at']} ===")
            for fresh, why, leg, name_pair in f["legs"]:
                fixture = f"{name_pair[0]} vs {name_pair[1]}" if name_pair else f"match #{leg['match_id']}"
                print(f"    {fixture}: {leg['market']} / {leg['selection']}  ->  {fresh}  ({why})")
            print()

        if not args.apply:
            print(f"Dry run -- {len(flagged)} row(s) would be reset to pending. Re-run with --apply to do it.")
            return

        for f in flagged:
            row = f["row"]
            row.result = RESULT_PENDING
            row.settled_at = None
            if hasattr(row, "leg_results"):
                row.leg_results = None
        db.commit()
        print(f"Reset {len(flagged)} row(s) to pending -- the next ordinary read re-settles each one for real.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
