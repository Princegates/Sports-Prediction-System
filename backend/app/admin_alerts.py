"""Emails the admins when something has quietly stopped working.

Two conditions, both of which otherwise go unnoticed until a member
complains:

- predictions are stale: the newest is older than ``STALE_PREDICTIONS_AFTER``
  (the daily refresh runs every morning, so 30 hours means one was missed);
- a payment is stuck: Hubtel says it was paid, but ``PAYMENT_STUCK_AFTER``
  after it started the member still hasn't been given what they paid for.

Checked from the payment sweep loop. Each kind of alert is sent at most once
per ``REPEAT_EVERY`` (held as a scheduler lease, so all workers agree), and
only when outbound email is configured.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import mailer, scheduler_lease
from app.db.models import Match, Payment, Prediction, User
from app.payments import hubtel
from app.timeutil import utcnow

logger = logging.getLogger(__name__)

STALE_PREDICTIONS_AFTER = dt.timedelta(hours=30)
PAYMENT_STUCK_AFTER = dt.timedelta(minutes=15)
REPEAT_EVERY = dt.timedelta(hours=6)


def problems(db: Session, now: dt.datetime | None = None) -> dict[str, str]:
    """kind -> a sentence for the admin, for each condition that holds now."""

    now = now or utcnow()
    found: dict[str, str] = {}

    newest = db.execute(select(func.max(Prediction.created_at))).scalar_one_or_none()
    # Only while there are matches to predict: an international break with
    # nothing scheduled isn't a missed refresh.
    upcoming = db.execute(
        select(func.count(Match.id)).where(
            Match.status == "SCHEDULED", Match.date > now, Match.date <= now + dt.timedelta(days=7)
        )
    ).scalar_one()
    if newest is not None and upcoming and now - newest > STALE_PREDICTIONS_AFTER:
        hours = int((now - newest).total_seconds() // 3600)
        found["stale_predictions"] = (
            f"The newest prediction is {hours} hours old, so the daily refresh hasn't run. "
            "Check the Refresh predictions workflow on GitHub."
        )

    unsettled = db.execute(
        select(Payment).where(
            Payment.status.in_(("pending", "fulfilling")), Payment.created_at <= now - PAYMENT_STUCK_AFTER
        )
    ).scalars()
    stuck = [p.client_reference for p in unsettled if hubtel.says_paid(p.provider_detail)]
    if stuck:
        found["stuck_payments"] = (
            f"{len(stuck)} payment(s) Hubtel reports as paid haven't been granted yet: "
            f"{', '.join(stuck[:10])}. Open Admin -> Payments and press \"Check with Hubtel\", "
            "or issue a code by hand."
        )
    return found


def send_alerts(db: Session, now: dt.datetime | None = None) -> list[str]:
    """Emails each current problem to every active superadmin, unless that
    kind was sent within REPEAT_EVERY. Returns the kinds sent."""

    found = problems(db, now)
    if not found or not mailer.is_configured(db):
        return []
    admins = [
        u.email for u in db.execute(
            select(User).where(User.role == "superadmin", User.status == "active")
        ).scalars()
    ]
    if not admins:
        return []
    sent: list[str] = []
    for kind, message in found.items():
        # A fresh holder each time: taking the lease succeeds only when no
        # earlier alert of this kind is still within its quiet period.
        if not scheduler_lease.acquire(db, f"alert:{kind}", REPEAT_EVERY, holder=uuid.uuid4().hex, now=now):
            continue
        for email in admins:
            mailer.send_email(email, "Socca Intelligence: needs your attention", message, db=db)
        logger.warning("Admin alert sent (%s): %s", kind, message)
        sent.append(kind)
    return sent
