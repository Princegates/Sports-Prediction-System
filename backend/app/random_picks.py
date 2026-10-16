"""Random "no risk tier, no target odds" AI Generation slips -- COUNT per
cadence (daily/weekly), published onto AdminPick alongside the hand-curated
Admin Picks and the weekly Low/Medium/High accumulators. AdminPick.source
splits the two cadences client-side ("system_random_daily_<n>" /
"system_random_weekly_<n>", n = 1..COUNT).

Reuses app.betcode.selection.select_random_legs end to end: the same "real
prediction, one leg per match, meaningful markets, AI_PICKS_LEAGUES only,
at least MIN_PROBABILITY" pool select_legs_by_confidence already searches
for AI Generation, just drawn at a random size (MIN_LEGS-MAX_LEGS) instead
of ranked and capped.

generate_cadence is the "given a DB session and a cadence, do one draw"
shape both scripts/generate_random_picks.py (kept as a manual/dry-run CLI
entry point) and app.main's own in-process scheduler need, so neither
re-derives the draw-and-publish logic on its own -- same split
run_live_sync_from_settings already has between scripts/sync_live_matches.py
and app.main's live-score loop. The daily cadence runs in-process
specifically because GitHub Actions' scheduled refresh-predictions.yml has
been landing 6-8 hours late every single day in production rather than at
its configured 06:00 UTC -- a platform-level scheduling limit, not a bug in
the draw itself (see app.main.LIVE_SYNC_INTERVAL_SECONDS's own comment for
the identical pattern already hit with a 5-minute cron). This draw needs no
external HTTP call, only predictions already in the database, which is what
makes running it in-process cheap enough to do at all.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.betcode.selection import AI_PICKS_LEAGUES, select_random_legs
from app.db.models import AdminPick

COUNT = 2
MIN_LEGS = 10
MAX_LEGS = 15
MIN_PROBABILITY = 0.60

# (cadence, days_ahead, display label).
CADENCES: dict[str, tuple[int, str]] = {
    "daily": (3, "Random Daily Pick"),
    "weekly": (10, "Random Weekly Pick"),
}

SOURCE_PREFIX = "system_random_"


def _source(cadence: str, n: int) -> str:
    return f"{SOURCE_PREFIX}{cadence}_{n}"


def _note(cadence: str) -> str:
    return (
        f"Randomly drawn from matches at {MIN_PROBABILITY:.0%}+ model probability, reshuffled {cadence} -- "
        "not ranked by risk level, and not the safest possible combination."
    )


def generate_cadence(db: Session, cadence: str, *, dry_run: bool = False) -> list[str]:
    """Draws and (unless ``dry_run``) writes COUNT slips for one cadence,
    returning a human-readable report line per draw or skip -- the caller
    decides what to do with those (print them, log them) and, unless
    dry_run, still owns committing the session afterward.

    A slot with fewer than 3 qualifying legs is left exactly as it was
    (skipped, not emptied or deleted) -- "stale but valid beats empty",
    the same reasoning generate_weekly_picks.py uses for its own tiers.
    """

    days_ahead, label = CADENCES[cadence]
    lines = [f"=== {cadence} ({days_ahead} days ahead) ==="]

    for n in range(1, COUNT + 1):
        result = select_random_legs(
            db, min_probability=MIN_PROBABILITY, min_legs=MIN_LEGS, max_legs=MAX_LEGS, days_ahead=days_ahead,
            leagues=AI_PICKS_LEAGUES,
        )
        source = _source(cadence, n)

        if len(result.legs) < 3:
            lines.append(f"[{n}] skipped -- {result.warnings[0] if result.warnings else 'not enough qualifying matches'}")
            continue

        lines.append(f"[{n}] {len(result.legs)} legs, combined probability {result.combined_probability:.1%}")
        for leg in result.legs:
            lines.append(
                f"    {leg.home_team} vs {leg.away_team} ({leg.league}): {leg.market} -- "
                f"{leg.selection} @ {leg.model_probability:.0%}"
            )

        if dry_run:
            continue

        pick = db.execute(select(AdminPick).where(AdminPick.source == source)).scalar_one_or_none()
        if pick is None:
            pick = AdminPick(source=source)
            db.add(pick)

        pick.legs = [{"match_id": leg.match_id, "market": leg.market, "selection": leg.selection} for leg in result.legs]
        pick.priced = False
        # No "#n" suffix when there's only one -- "#1" of one reads like a
        # typo, not a count.
        pick.label = label if COUNT == 1 else f"{label} #{n}"
        pick.note = _note(cadence)
        # The legs just changed, so any code an admin pasted onto the
        # previous draw no longer matches this one.
        pick.booking_code = None
        pick.booking_code_bookmaker = None
        pick.created_by_user_id = None
        pick.expires_at = max(leg.kickoff for leg in result.legs) + dt.timedelta(days=2)

    if not dry_run:
        # A lower COUNT than a previous deploy's (e.g. the 5->1 cut this
        # comment was added for) leaves rows 2..old-COUNT behind forever
        # otherwise -- stale draws nobody can reach by regenerating, but
        # still rendered by RandomPicksSection since it reads every row
        # matching the prefix, not just the first COUNT of them.
        prefix = f"{SOURCE_PREFIX}{cadence}_"
        existing = db.execute(select(AdminPick).where(AdminPick.source.startswith(prefix))).scalars().all()
        for pick in existing:
            try:
                n = int(pick.source[len(prefix):])
            except ValueError:
                continue
            if n > COUNT:
                db.delete(pick)

    return lines
