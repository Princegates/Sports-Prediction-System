#!/usr/bin/env python3
"""Read-only: run the exact re-resolution /api/admin-picks runs on every
stored AdminPick row -- resolve_legs_unpriced (or price_legs for a priced
pick) against the *current* Prediction/Match state -- and print, per leg,
whether it still resolves and why not when it doesn't. The route drops an
entire pick the moment even one leg fails to resolve, so this is the one
place that explains a pick that's in the database but not on the Dashboard.
Changes nothing.

    python scripts/diagnose_admin_picks_render.py --source-prefix system_random
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse

from sqlalchemy import select

from app import app_settings
from app.betcode.selection import price_legs, resolve_legs_unpriced
from app.db.models import AdminPick, Match, Prediction
from app.db.session import SessionLocal
from app.outcomes.registry import find_outcome, outcomes_from_prediction


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source-prefix", default="system_random")
    args = parser.parse_args()

    sys.stdout.reconfigure(line_buffering=True)

    db = SessionLocal()
    try:
        now = dt.datetime.utcnow()
        values = app_settings.all_values(db)
        print(f"admin_picks_enabled = {values.get('admin_picks_enabled', True)!r}")
        print(f"admin_picks_free_tier_visible = {values.get('admin_picks_free_tier_visible', True)!r}\n")
        picks = list(
            db.execute(select(AdminPick).where(AdminPick.source.like(f"{args.source_prefix}%"))).scalars()
        )
        print(f"Found {len(picks)} AdminPick row(s) with source like {args.source_prefix!r}\n")

        for pick in picks:
            print(f"=== pick id={pick.id} source={pick.source!r} label={pick.label!r} result={pick.result!r} "
                  f"priced={pick.priced} expires_at={pick.expires_at} ===")
            refs = [(leg["match_id"], leg["market"], leg["selection"]) for leg in pick.legs]
            print(f"  stored legs: {len(refs)}")

            if pick.result != "pending":
                print("  (not pending -- admin_picks route would use frozen_admin_pick_legs instead; skipping live re-resolution)")
                print()
                continue
            if pick.expires_at is not None and pick.expires_at <= now:
                print(f"  *** expires_at ({pick.expires_at}) is already past -- admin_picks route drops this pick outright. ***")
                print()
                continue

            legs, warnings = price_legs(db, refs) if pick.priced else resolve_legs_unpriced(db, refs)
            print(f"  resolved legs: {len(legs)} / {len(refs)}  {'-- MATCH, pick would show' if len(legs) == len(refs) else '*** MISMATCH -- pick is dropped from /api/admin-picks ***'}")
            for w in warnings:
                print(f"    warning: {w}")

            if len(legs) != len(refs):
                print("  per-leg detail:")
                for match_id, market, selection in refs:
                    match = db.get(Match, match_id)
                    if match is None:
                        print(f"    match#{match_id}: NOT FOUND")
                        continue
                    prediction = db.execute(
                        select(Prediction).where(Prediction.match_id == match_id).order_by(Prediction.created_at.desc())
                    ).scalars().first()
                    status = match.status
                    has_pred = prediction is not None
                    outcome = find_outcome(prediction, market, selection) if prediction else None
                    all_outcomes = outcomes_from_prediction(prediction) if prediction else []
                    print(
                        f"    match#{match_id} status={status!r} has_prediction={has_pred} "
                        f"total_outcomes={len(all_outcomes)} wanted=({market!r},{selection!r}) found={outcome is not None}"
                    )
            print()
    finally:
        db.close()


if __name__ == "__main__":
    main()
