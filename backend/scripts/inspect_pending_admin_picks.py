#!/usr/bin/env python3
"""Read-only. Lists every still-pending AdminPick (Admin Picks, This Week's
Picks, jackpots, Random Picks -- every source pick_settlement.py tracks for
that table) with each leg's current state: the fixture, its kickoff, whether
app.db.models.Match.is_finished considers it over yet, and -- once it is --
app.outcomes.grading.grade_outcome's fresh verdict on it.

Exists to answer "I won one of the Admin Picks slips, why isn't it showing
won" without guessing: an accumulator only reaches "won" once *every* leg in
it is confirmed won (see app/pick_settlement.py's _overall_result), so one
leg finishing and winning does not move the slip at all while another leg is
still unplayed, or landed on a market grade_outcome can't auto-grade
("unresolved" -- needs a Super Admin's manual call, not more time).

Changes nothing -- same "fresh regrade off grade_outcome" logic
audit_settled_picks.py uses, just printed per leg instead of compared
against a frozen result (there is nothing frozen yet for a pending row).

    python scripts/inspect_pending_admin_picks.py
    python scripts/inspect_pending_admin_picks.py --source system_weekly_low
    python scripts/inspect_pending_admin_picks.py --team Arsenal
    python scripts/inspect_pending_admin_picks.py --team Arsenal --include-settled
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AdminPick, Match, Team
from app.db.session import SessionLocal
from app.outcomes.grading import grade_outcome
from app.pick_settlement import RESULT_LOST, RESULT_PENDING, RESULT_UNRESOLVED, RESULT_WON


def _team_names(db: Session, match_ids: set[int]) -> dict[int, tuple[str, str]]:
    if not match_ids:
        return {}
    matches = db.execute(select(Match).where(Match.id.in_(match_ids))).scalars().all()
    team_ids = {t for m in matches for t in (m.home_team_id, m.away_team_id)}
    names = dict(db.execute(select(Team.id, Team.name).where(Team.id.in_(team_ids))).all())
    return {m.id: (names.get(m.home_team_id, "?"), names.get(m.away_team_id, "?")) for m in matches}


def _leg_status(match: Match | None, market: str, selection: str) -> tuple[str, str]:
    if match is None:
        return RESULT_PENDING, "match no longer exists"
    if not match.is_finished:
        return RESULT_PENDING, f"status={match.status!r}, kickoff {match.date:%Y-%m-%d %H:%M} UTC"
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", default=None, help="Only AdminPick.source exactly matching this (blank = every pending row)")
    parser.add_argument("--team", default=None, help="Only slips with a leg involving a club whose name contains this")
    parser.add_argument(
        "--include-settled", action="store_true",
        help="Also show already-settled rows (their frozen result/leg_results, not a fresh regrade) -- for checking what a pick actually settled to after it leaves pending.",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        stmt = select(AdminPick).order_by(AdminPick.created_at.desc())
        if not args.include_settled:
            stmt = stmt.where(AdminPick.result == RESULT_PENDING)
        if args.source:
            stmt = stmt.where(AdminPick.source == args.source)
        picks = db.execute(stmt).scalars().all()

        match_ids = {leg["match_id"] for pick in picks for leg in pick.legs}
        names = _team_names(db, match_ids)
        matches = {m.id: m for m in db.execute(select(Match).where(Match.id.in_(match_ids))).scalars()}

        needle = args.team.lower() if args.team else None
        shown = 0
        for pick in picks:
            leg_names = [names.get(leg["match_id"]) for leg in pick.legs]
            if needle is not None and not any(nm and (needle in nm[0].lower() or needle in nm[1].lower()) for nm in leg_names):
                continue
            shown += 1

            frozen = pick.result != RESULT_PENDING
            header = f"\n[{pick.source or 'admin (hand-curated)'}] AdminPick #{pick.id} -- {pick.label}"
            if frozen:
                header += f"  [FROZEN: {pick.result.upper()} @ {pick.settled_at}]"
            # The two flags that decide whether GET /api/predictions/admin-picks
            # (what the app's Admin Picks section actually calls) includes this
            # row at all -- public_teaser=True excludes it outright (that's the
            # unauthenticated Welcome page's own pick, a different slot), and
            # featured_public only matters once settled (gates the public track
            # record, not this listing). A pick can be perfectly valid and
            # pending/won and still never show up here if public_teaser is set.
            if pick.public_teaser:
                header += "  [PUBLIC_TEASER -- excluded from the Admin Picks list, shown on the public Welcome page instead]"
            print(header)

            won = lost = pending = unresolved = 0
            for i, (leg, nm) in enumerate(zip(pick.legs, leg_names)):
                match = matches.get(leg["match_id"])
                status, why = _leg_status(match, leg["market"], leg["selection"])
                if status == RESULT_WON:
                    won += 1
                elif status == RESULT_LOST:
                    lost += 1
                elif status == RESULT_UNRESOLVED:
                    unresolved += 1
                else:
                    pending += 1
                fixture = f"{nm[0]} vs {nm[1]}" if nm else f"match {leg['match_id']}"
                line = f"    {status.upper():<10} {fixture}: {leg['market']} -- {leg['selection']} ({why})"
                if frozen and pick.leg_results and i < len(pick.leg_results):
                    frozen_leg = pick.leg_results[i]
                    if frozen_leg != status:
                        line += f"  [FROZEN LEG SAYS: {frozen_leg.upper()} -- MISMATCH]"
                print(line)

            if frozen:
                continue  # the row's own [FROZEN: ...] header above is the real answer

            if lost:
                verdict = "would settle LOST (at least one leg lost)"
            elif pending:
                verdict = f"still PENDING ({pending} leg(s) not finished yet)"
            elif unresolved:
                verdict = f"would settle UNRESOLVED ({unresolved} leg(s) need a Super Admin's manual call)"
            else:
                verdict = "would settle WON on the next read -- every leg already resolves won"
            print(f"    -> {verdict}")

        if shown == 0:
            print("No pending AdminPick rows match that filter.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
