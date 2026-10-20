"""Rules for the public track record of admin-chosen picks.

A pick enters the record only by being committed *before its first match
kicks off*, and stays in it whatever happens. The old rule was the reverse
-- a pick could only be featured once it had settled -- so the record, and
the hit rate the landing page leads with, was whichever winners an admin
chose to show after the fact.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import AdminPick, FeaturedPick, Match


def first_kickoff(db: Session, match_ids: Iterable[int]) -> dt.datetime | None:
    ids = list(match_ids)
    if not ids:
        return None
    return db.execute(select(func.min(Match.date)).where(Match.id.in_(ids))).scalar_one_or_none()


def pick_match_ids(pick: AdminPick | FeaturedPick) -> list[int]:
    if isinstance(pick, FeaturedPick):
        return [pick.match_id]
    return [leg["match_id"] for leg in pick.legs]


def commitment_open(pick: AdminPick | FeaturedPick, kickoff: dt.datetime | None, now: dt.datetime | None = None) -> bool:
    """Whether the pick can still be added to, or taken out of, the public
    record: still pending, and its first match hasn't started."""

    now = now or dt.datetime.utcnow()
    return pick.result == "pending" and kickoff is not None and kickoff > now


def set_featured(db: Session, pick: AdminPick | FeaturedPick, featured: bool, *, now: dt.datetime | None = None) -> None:
    """Apply an admin's feature/unfeature, or raise ``ValueError`` with the
    reason it's no longer allowed."""

    now = now or dt.datetime.utcnow()
    if pick.featured_public == featured:
        return
    if not commitment_open(pick, first_kickoff(db, pick_match_ids(pick)), now):
        if featured:
            raise ValueError(
                "Picks can only join the public track record before kickoff -- adding one after its result "
                "is known would make the record a list of chosen winners."
            )
        raise ValueError("This pick is committed to the public track record. From kickoff on it stays, win or lose.")
    pick.featured_public = featured
    pick.featured_public_at = now if featured else None
