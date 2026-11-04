"""The daily Low / Medium / High risk accumulators on the AI Picks page.

Three slips, each 10-15 legs, pooled across AI_PICKS_LEAGUES (the biggest,
best-covered leagues) and published as AdminPick rows alongside the other
system picks -- ``AdminPick.source`` ("system_daily_low" / "_medium" /
"_high") tells them apart, one row per tier.

What makes a tier is the *per-leg* model probability, not a combined price:

    low     every leg 85%+ likely
    medium  every leg 70-85% likely
    high    every leg 55-70% likely

Unpriced on purpose. The weekly Low/Medium/High slips (scripts/
generate_weekly_picks.py) are defined by combined bookmaker *odds*, which
needs a captured price on every leg -- and when none are captured they
publish nothing at all ("Only 0 qualifying match(es) available"), which is
what happened the week they went missing. A probability band needs only the
predictions already in the database, so this can run in-process every hour
like the random picks do (see app.main), independent of GitHub's scheduler.

A tier's slip is replaced when it's no longer fully bookable: a new UTC day
has started, or its first match is about to kick off. The second rule is
not cosmetic -- a pending AdminPick vanishes from the page the moment any
one of its matches finishes (see routes_predictions.admin_picks), so a slip
left running past its first kickoff would disappear mid-afternoon. The
three tiers are kept on different matches where there are enough of them,
so one upset doesn't sink all three.

A tier that can't reach MIN_LEGS is skipped and whatever it showed before is
left as it was -- stale but valid beats empty, same as the other generators.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.betcode.selection import AI_PICKS_LEAGUES, ConfidencePick, select_band_legs
from app.db.models import AdminPick, Match
from app.timeutil import utcnow

MIN_LEGS = 10
MAX_LEGS = 15

# (tier, lowest leg probability, highest leg probability -- exclusive).
TIERS: tuple[tuple[str, float, float], ...] = (
    ("low", 0.85, 1.01),
    ("medium", 0.70, 0.85),
    ("high", 0.55, 0.70),
)

BAND_TEXT = {
    "low": "every leg 85%+ likely",
    "medium": "every leg 70-85% likely",
    "high": "every leg 55-70% likely",
}

# Tried in order: the smallest window that can fill a slip wins, so a busy
# weekend uses the next day's matches and a quiet midweek stretches further.
WINDOWS_DAYS = (1, 2, 3, 5)

SOURCE_PREFIX = "system_daily_"

# A slip whose first match starts within this long is already as good as
# started -- not something to hand a member to book.
KICKOFF_BUFFER = dt.timedelta(minutes=15)


def _label(tier: str) -> str:
    return f"{tier.capitalize()} Risk -- Daily Accumulator"


def _note(now: dt.datetime, leg_count: int, days: int) -> str:
    return (
        f"Auto-generated {now:%d %b}: {leg_count} matches from the next {days} day(s) across the biggest "
        "leagues. Replaced when its first match kicks off."
    )


def _is_current(db: Session, pick: AdminPick, now: dt.datetime) -> bool:
    """Generated today and still fully upcoming."""

    if pick.created_at.date() != now.date():
        return False
    ids = [leg["match_id"] for leg in pick.legs]
    if not ids:
        return False
    first_kickoff, found = db.execute(
        select(func.min(Match.date), func.count(Match.id)).where(Match.id.in_(ids))
    ).one()
    return found == len(set(ids)) and first_kickoff is not None and first_kickoff > now + KICKOFF_BUFFER


def _build(
    db: Session, low: float, high: float, used: set[int], avoid: set[int]
) -> tuple[ConfidencePick, int] | None:
    """A full slip for one band, preferring matches the other tiers haven't
    taken, and falling back to sharing them only if there aren't enough.
    ``avoid`` (matches about to start) is never used either way."""

    attempts = [frozenset(used | avoid)] if not used else [frozenset(used | avoid), frozenset(avoid)]
    for exclude in attempts:
        for days in WINDOWS_DAYS:
            result = select_band_legs(
                db, min_probability=low, max_probability=high, min_legs=MIN_LEGS, max_legs=MAX_LEGS,
                leagues=AI_PICKS_LEAGUES, days_ahead=days, exclude_match_ids=exclude,
            )
            if len(result.legs) >= MIN_LEGS:
                return result, days
    return None


def generate_daily(
    db: Session, *, now: dt.datetime | None = None, dry_run: bool = False, force: bool = False
) -> list[str]:
    """Brings each tier's slip up to date, returning a report line per tier.
    The caller owns committing the session (unless ``dry_run``).

    A replaced slip is deleted and recreated rather than edited in place, so
    it starts with a fresh created_at and a clean "pending" result -- an
    edited row would carry the last slip's settlement over onto new legs.
    """

    now = now or utcnow()
    existing = {
        pick.source: pick
        for pick in db.execute(select(AdminPick).where(AdminPick.source.startswith(SOURCE_PREFIX))).scalars()
    }
    used: set[int] = set()
    lines: list[str] = []
    # A match that's about to start can't anchor a slip -- it would count as
    # stale (see _is_current) on the very next run. Measured on the real
    # clock, which is what the match search itself runs on (``now`` only
    # decides whether a slip is from today).
    avoid = set(db.execute(select(Match.id).where(Match.date <= utcnow() + KICKOFF_BUFFER)).scalars())

    for tier, low, high in TIERS:
        source = f"{SOURCE_PREFIX}{tier}"
        current = existing.get(source)

        if current is not None and not force and _is_current(db, current, now):
            used |= {leg["match_id"] for leg in current.legs}
            lines.append(f"[{tier}] current -- kept")
            continue

        built = _build(db, low, high, used, avoid)
        if built is None:
            kept = "kept the previous slip" if current is not None else "nothing to show yet"
            lines.append(
                f"[{tier}] skipped -- fewer than {MIN_LEGS} matches in the next {WINDOWS_DAYS[-1]} days "
                f"have {BAND_TEXT[tier]}; {kept}"
            )
            if current is not None:
                used |= {leg["match_id"] for leg in current.legs}
            continue

        result, days = built
        used |= {leg.match_id for leg in result.legs}
        lines.append(
            f"[{tier}] {len(result.legs)} legs from the next {days} day(s), "
            f"combined probability {result.combined_probability:.1%}"
        )
        for leg in result.legs:
            lines.append(
                f"    {leg.home_team} vs {leg.away_team} ({leg.league}): {leg.market} -- "
                f"{leg.selection} @ {leg.model_probability:.0%}"
            )
        if dry_run:
            continue

        if current is not None:
            db.delete(current)
        db.add(
            AdminPick(
                source=source,
                legs=[{"match_id": leg.match_id, "market": leg.market, "selection": leg.selection} for leg in result.legs],
                priced=False,
                label=_label(tier),
                note=_note(now, len(result.legs), days),
                created_by_user_id=None,
                expires_at=max(leg.kickoff for leg in result.legs) + dt.timedelta(days=2),
            )
        )

    return lines
