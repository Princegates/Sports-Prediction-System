"""Access-code generation and redemption.

There is no in-app payment gateway: a buyer pays a superadmin outside the
platform -- mobile money, bank transfer, cash -- and the superadmin
generates a code for them here. Three tables back this rather than one, on
purpose: ``AccessCode`` is the voucher itself, ``AccessGrant`` is the
permission window redeeming it opens, and ``AccessRedemption`` is the
immutable record that it happened. Splitting them means extending or
revoking a grant later never touches the code's own history, and a code
good for several redemptions produces one grant per redeemer instead of one
row trying to be all three things at once.
"""

from __future__ import annotations

import datetime as dt
import secrets

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.models import AccessCode, AccessGrant, AccessRedemption, AuditLog, User

# Excludes 0/O and 1/I -- a code is read off a screenshot or typed from a
# text message, and those pairs are exactly the ones that get misread.
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_SEGMENTS = 3
_SEGMENT_LENGTH = 4


class AccessCodeError(Exception):
    """A redemption or admin action was rejected for a reason the caller
    should show verbatim to whoever triggered it."""


def generate_code_string() -> str:
    segments = ["".join(secrets.choice(_ALPHABET) for _ in range(_SEGMENT_LENGTH)) for _ in range(_SEGMENTS)]
    return "-".join(segments)


def mask_code(code: str) -> str:
    """The full code is shown once, at creation. Every later view -- admin
    list, detail, audit log -- shows this instead, so a code isn't
    recoverable by anyone who can merely see the admin dashboard."""

    segments = code.split("-")
    if len(segments) < 2:
        return "*" * max(len(code) - 4, 0) + code[-4:]
    return "-".join(["*" * len(s) for s in segments[:-1]] + [segments[-1]])


def normalize_code(raw: str) -> str:
    return raw.strip().upper()


def access_code_effective_status(code: AccessCode) -> str:
    """``code.status`` only ever stores active/revoked -- exhausted and
    expired are derived here so they can't drift out of sync with
    ``redemption_count`` or ``code_expires_at``."""

    if code.status == "revoked":
        return "revoked"
    if code.code_expires_at is not None and dt.datetime.utcnow() > code.code_expires_at:
        return "expired"
    if code.redemption_count >= code.redemption_limit:
        return "exhausted"
    return "active"


def access_grant_effective_status(grant: AccessGrant) -> str:
    if grant.status == "revoked":
        return "revoked"
    if grant.expires_at <= dt.datetime.utcnow():
        return "expired"
    return "active"


def current_grant(db: Session, user: User) -> AccessGrant | None:
    """The grant that decides whether ``user`` has feature access right now,
    if any -- the most recent one that hasn't been revoked, whether or not
    it has since expired (the caller checks that separately: 'you had access
    and it lapsed' and 'you never had any' read differently in the UI)."""

    return (
        db.execute(
            select(AccessGrant)
            .where(AccessGrant.user_id == user.id, AccessGrant.status == "active")
            .order_by(AccessGrant.created_at.desc())
        )
        .scalars()
        .first()
    )


def create_access_code(
    db: Session,
    admin: User,
    *,
    duration_days: int,
    redemption_limit: int = 1,
    code_expires_in_days: int | None = None,
    assigned_user_id: int | None = None,
    notes: str | None = None,
) -> AccessCode:
    if duration_days <= 0:
        raise AccessCodeError("Duration must be at least 1 day.")
    if redemption_limit <= 0:
        raise AccessCodeError("Redemption limit must be at least 1.")

    code_expires_at = None
    if code_expires_in_days is not None:
        code_expires_at = dt.datetime.utcnow() + dt.timedelta(days=code_expires_in_days)

    # 33^12 possibilities -- a collision is astronomically unlikely, but a
    # silent one would let one buyer's code redeem into a stranger's slot, so
    # it's checked and retried rather than trusted blindly.
    for _ in range(5):
        candidate = generate_code_string()
        if db.execute(select(AccessCode).where(AccessCode.code == candidate)).scalar_one_or_none() is None:
            break
    else:
        raise AccessCodeError("Could not generate a unique code -- try again.")

    access_code = AccessCode(
        code=candidate,
        duration_days=duration_days,
        redemption_limit=redemption_limit,
        code_expires_at=code_expires_at,
        assigned_user_id=assigned_user_id,
        created_by_user_id=admin.id,
        notes=notes,
    )
    db.add(access_code)
    db.flush()

    db.add(
        AuditLog(
            actor_user_id=admin.id,
            actor_email=admin.email,
            action="access_code.created",
            detail={
                "access_code_id": access_code.id,
                "duration_days": duration_days,
                "redemption_limit": redemption_limit,
                "assigned_user_id": assigned_user_id,
            },
        )
    )
    db.commit()
    db.refresh(access_code)
    return access_code


def revoke_access_code(db: Session, admin: User, access_code: AccessCode, reason: str | None = None) -> AccessCode:
    if access_code.status == "revoked":
        raise AccessCodeError("This code is already revoked.")

    access_code.status = "revoked"
    access_code.revoked_at = dt.datetime.utcnow()
    access_code.revoked_reason = reason

    db.add(
        AuditLog(
            actor_user_id=admin.id,
            actor_email=admin.email,
            action="access_code.revoked",
            detail={"access_code_id": access_code.id, "reason": reason},
        )
    )
    db.commit()
    db.refresh(access_code)
    return access_code


def extend_grant(db: Session, admin: User, user: User, additional_days: int) -> AccessGrant:
    """Push a user's current grant's expiry out. Requires a grant to already
    exist -- a user who has never redeemed anything has nothing to extend,
    and the fix for that is a new assigned access code, not this."""

    if additional_days <= 0:
        raise AccessCodeError("Extension must be at least 1 day.")

    grant = current_grant(db, user)
    if grant is None:
        raise AccessCodeError("This user has no grant to extend -- generate an access code for them instead.")

    now = dt.datetime.utcnow()
    base = grant.expires_at if grant.expires_at > now else now
    grant.expires_at = base + dt.timedelta(days=additional_days)

    db.add(
        AuditLog(
            actor_user_id=admin.id,
            actor_email=admin.email,
            action="access_grant.extended",
            target_user_id=user.id,
            detail={"additional_days": additional_days, "new_expires_at": grant.expires_at.isoformat()},
        )
    )
    db.commit()
    db.refresh(grant)
    return grant


def revoke_current_grant(db: Session, admin: User, user: User, reason: str | None = None) -> AccessGrant:
    grant = current_grant(db, user)
    if grant is None:
        raise AccessCodeError("This user has no active grant to revoke.")

    grant.status = "revoked"
    grant.revoked_at = dt.datetime.utcnow()
    grant.revoked_reason = reason

    db.add(
        AuditLog(
            actor_user_id=admin.id,
            actor_email=admin.email,
            action="access_grant.revoked",
            target_user_id=user.id,
            detail={"reason": reason},
        )
    )
    db.commit()
    db.refresh(grant)
    return grant


def redeem_access_code(db: Session, user: User, raw_code: str) -> AccessGrant:
    """Validate and redeem a code for ``user``, returning the grant it opens.

    The capacity check is a single conditional UPDATE
    (``redemption_count < redemption_limit``) rather than a read-then-write,
    so two people redeeming a code's last slot at the same instant can't both
    succeed -- the database's own row locking during the UPDATE decides which
    one wins, and the loser gets a clean "fully redeemed" error instead of
    silently over-issuing access.
    """

    normalized = normalize_code(raw_code)
    if not normalized:
        raise AccessCodeError("Enter an access code.")

    code = db.execute(select(AccessCode).where(AccessCode.code == normalized)).scalar_one_or_none()
    if code is None:
        raise AccessCodeError("That code doesn't exist.")

    now = dt.datetime.utcnow()

    if code.status == "revoked":
        raise AccessCodeError("This code has been revoked.")
    if code.code_expires_at is not None and now > code.code_expires_at:
        raise AccessCodeError("This code has expired.")
    if code.assigned_user_id is not None and code.assigned_user_id != user.id:
        raise AccessCodeError("This code is assigned to a different account.")

    duplicate = db.execute(
        select(AccessRedemption).where(
            AccessRedemption.access_code_id == code.id, AccessRedemption.user_id == user.id
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        raise AccessCodeError("You've already redeemed this code.")

    result = db.execute(
        update(AccessCode)
        .where(AccessCode.id == code.id, AccessCode.redemption_count < AccessCode.redemption_limit)
        .values(redemption_count=AccessCode.redemption_count + 1)
    )
    if result.rowcount == 0:
        raise AccessCodeError("This code has already been fully redeemed.")

    existing = current_grant(db, user)
    base_time = now
    if existing is not None and existing.expires_at > now:
        # Redeeming while still covered extends the remaining time rather
        # than discarding it -- superseding the old row keeps one grant
        # "active" per user at a time without losing the stacked duration.
        base_time = existing.expires_at
        existing.status = "revoked"
        existing.revoked_at = now
        existing.revoked_reason = "superseded by new code redemption"

    expires_at = base_time + dt.timedelta(days=code.duration_days)
    grant = AccessGrant(user_id=user.id, access_code_id=code.id, expires_at=expires_at)
    db.add(grant)
    db.flush()

    db.add(AccessRedemption(access_code_id=code.id, user_id=user.id, grant_id=grant.id))
    db.add(
        AuditLog(
            actor_user_id=user.id,
            actor_email=user.email,
            action="access_code.redeemed",
            target_user_id=user.id,
            detail={"access_code_id": code.id, "expires_at": expires_at.isoformat()},
        )
    )
    db.commit()
    db.refresh(grant)
    return grant
