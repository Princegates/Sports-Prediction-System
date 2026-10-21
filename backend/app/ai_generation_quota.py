"""Per-user daily cap on AI Generation calls (routes_betcodes.suggest and
suggest_random -- the Refresh button hits suggest again, so it counts the
same way).

Admin-configurable globally (app_settings.ai_generation_daily_limit), with
an optional per-user override (User.daily_ai_generation_limit_override) for
a member who pays for more. A superadmin is never limited, same bypass
idiom as app.access's extend_grant/revoke_current_grant.

Modeled as a rolling 24-hour window, not a calendar day -- same reasoning as
app.assistant.engine.llm_quota_left: a user who calls at 11:59pm and again
at 12:01am shouldn't get two days' worth back to back.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.timeutil import utcnow
from app import app_settings
from app.db.models import AIGenerationLog, User


def daily_limit(db: Session, user: User) -> int | None:
    """The cap that applies to ``user`` right now. ``None`` means
    unlimited (superadmins only -- an override is always a real number,
    including 0)."""

    if user.role == "superadmin":
        return None
    if user.daily_ai_generation_limit_override is not None:
        return user.daily_ai_generation_limit_override
    return int(app_settings.get_value(db, "ai_generation_daily_limit") or 0)


def used_today(db: Session, user_id: int, now: dt.datetime | None = None) -> int:
    now = now or utcnow()
    return db.execute(
        select(func.count(AIGenerationLog.id)).where(
            AIGenerationLog.user_id == user_id,
            AIGenerationLog.created_at >= now - dt.timedelta(hours=24),
        )
    ).scalar_one()


def quota_remaining(db: Session, user: User, now: dt.datetime | None = None) -> int | None:
    """``None`` means unlimited. Never negative."""

    limit = daily_limit(db, user)
    if limit is None:
        return None
    return max(limit - used_today(db, user.id, now), 0)


def has_quota_left(db: Session, user: User, now: dt.datetime | None = None) -> bool:
    remaining = quota_remaining(db, user, now)
    return remaining is None or remaining > 0


def record_generation(db: Session, user: User, now: dt.datetime | None = None) -> None:
    db.add(AIGenerationLog(user_id=user.id, created_at=now or utcnow()))
    db.commit()
