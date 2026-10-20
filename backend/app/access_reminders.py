"""Automatic "your access ends soon" email, a day before a grant expires.

The only nudge before this was scripts/send_access_reminder_email.py, run by
hand -- so most trials simply lapsed without the account ever being told,
at exactly the point where it was most likely to pay. Run hourly from
app.main's background loops; each grant is reminded at most once.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import app_settings, mailer
from app.db.models import AccessCode, AccessGrant, AuditLog, User

# A grant expiring 20-28 hours from now gets its reminder. Eight hours of
# overlap means an hourly loop that misses a few runs still catches it.
REMIND_FROM = dt.timedelta(hours=20)
REMIND_UNTIL = dt.timedelta(hours=28)
# Don't remind about a grant that only just started (a one-day trial would
# otherwise get "ends soon" minutes after signup).
MIN_GRANT_AGE = dt.timedelta(hours=12)
ACTION = "access.expiry_reminder_sent"


def send_expiry_reminders(db: Session, *, now: dt.datetime | None = None) -> int:
    """Email every account whose access ends in about a day and hasn't been
    told yet. Returns how many were sent. A no-op without outbound email."""

    if not mailer.is_configured(db):
        return 0
    now = now or dt.datetime.utcnow()

    rows = db.execute(
        select(AccessGrant, User, AccessCode)
        .join(User, User.id == AccessGrant.user_id)
        .join(AccessCode, AccessCode.id == AccessGrant.access_code_id)
        .where(
            AccessGrant.status == "active",
            AccessGrant.expires_at >= now + REMIND_FROM,
            AccessGrant.expires_at <= now + REMIND_UNTIL,
            AccessGrant.activated_at <= now - MIN_GRANT_AGE,
            User.status != "suspended",
            User.role != "superadmin",
        )
    ).all()
    if not rows:
        return 0

    already = {
        (entry.target_user_id, (entry.detail or {}).get("grant_id"))
        for entry in db.execute(
            select(AuditLog).where(AuditLog.action == ACTION, AuditLog.created_at >= now - dt.timedelta(days=3))
        ).scalars()
    }

    site_url = mailer.resolve_config(db).site_url or None
    whatsapp = str(app_settings.get_value(db, "contact_whatsapp") or "") or None
    price = str(app_settings.get_value(db, "pricing_label") or "") or None

    sent = 0
    for grant, user, code in rows:
        if (user.id, grant.id) in already:
            continue
        hours_left = max(1, round((grant.expires_at - now).total_seconds() / 3600))
        subject, body = mailer.access_ending_message(
            hours_left, trial=code.kind == "trial", site_url=site_url, whatsapp=whatsapp, price_label=price
        )
        result = mailer.send_email(user.email, subject, body, db=db)
        if not result.sent:
            continue  # tried again next hour while still inside the window
        db.add(AuditLog(actor_user_id=None, actor_email=None, action=ACTION, target_user_id=user.id,
                        detail={"grant_id": grant.id, "expires_at": grant.expires_at.isoformat()}))
        db.commit()
        sent += 1
    return sent
