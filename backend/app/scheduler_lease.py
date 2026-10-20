"""One runner per background loop, across every process sharing the
database -- see db.models.SchedulerLease for why a row and not a lock.

Each loop iteration calls ``acquire`` first and skips its work when another
process holds the lease. The holder renews it every iteration; a process
that dies simply stops renewing, and the lease falls to whoever asks next
once it expires.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import delete, or_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.timeutil import utcnow
from app.db.models import SchedulerLease

# Identifies this process for the life of the import.
PROCESS_ID = uuid.uuid4().hex


def acquire(db: Session, name: str, ttl: dt.timedelta, *, holder: str = PROCESS_ID, now: dt.datetime | None = None) -> bool:
    """Take or renew the lease ``name`` for ``ttl``. Returns whether this
    holder now has it. Atomic: the conditional UPDATE (or the primary-key
    INSERT for a lease that doesn't exist yet) lets exactly one caller win."""

    now = now or utcnow()
    expires_at = now + ttl
    result = db.execute(
        update(SchedulerLease)
        .where(SchedulerLease.name == name, or_(SchedulerLease.holder == holder, SchedulerLease.expires_at < now))
        .values(holder=holder, expires_at=expires_at)
    )
    if result.rowcount:
        db.commit()
        return True

    db.add(SchedulerLease(name=name, holder=holder, expires_at=expires_at))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return False  # someone else holds it, unexpired
    return True


def release_all(db: Session, *, holder: str = PROCESS_ID) -> None:
    """Give up every lease this process holds -- called on clean shutdown."""

    db.execute(delete(SchedulerLease).where(SchedulerLease.holder == holder))
    db.commit()
