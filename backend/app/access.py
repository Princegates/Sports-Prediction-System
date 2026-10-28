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

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.timeutil import utcnow
from app import app_settings
from app.db.models import AccessCode, AccessGrant, AccessRedemption, AuditLog, User

# Excludes 0/O and 1/I -- a code is read off a screenshot or typed from a
# text message, and those pairs are exactly the ones that get misread.
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_SEGMENTS = 3
_SEGMENT_LENGTH = 4

# Shorter and un-dashed, unlike an access code -- this one is meant to sit
# in a URL (?ref=CODE) and get typed from memory, not read off a screenshot.
_REFERRAL_CODE_LENGTH = 7


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
    if code.code_expires_at is not None and utcnow() > code.code_expires_at:
        return "expired"
    if code.redemption_count >= code.redemption_limit:
        return "exhausted"
    return "active"


def access_grant_effective_status(grant: AccessGrant) -> str:
    if grant.status == "revoked":
        return "revoked"
    if grant.expires_at <= utcnow():
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


def had_signup_trial(db: Session, user: User) -> bool:
    return (
        db.execute(
            select(AccessCode.id).where(AccessCode.assigned_user_id == user.id, AccessCode.kind == "trial").limit(1)
        ).first()
        is not None
    )


def activation_pending(db: Session, user: User) -> bool:
    """A member whose free access is still waiting on their email
    confirmation: signup asks for it, they haven't clicked the link, and the
    trial it unlocks was never started. False for a Super Admin, and for an
    account that predates confirmation (it already had its trial, so there's
    nothing left to activate)."""

    if user.role == "superadmin" or user.email_verified_at is not None:
        return False
    if not app_settings.get_value(db, "email_verification_required"):
        return False
    return not had_signup_trial(db, user)


def count_activation_pending(db: Session) -> int:
    """How many active members are waiting on activation -- the same rule as
    ``activation_pending``, counted in one query for the admin overview."""

    if not app_settings.get_value(db, "email_verification_required"):
        return 0
    has_trial = select(AccessCode.assigned_user_id).where(
        AccessCode.kind == "trial", AccessCode.assigned_user_id.is_not(None)
    )
    return db.execute(
        select(func.count(User.id)).where(
            User.role != "superadmin",
            User.status == "active",
            User.email_verified_at.is_(None),
            User.id.not_in(has_trial),
        )
    ).scalar_one()


def create_access_code(
    db: Session,
    admin: User,
    *,
    duration_days: int,
    redemption_limit: int = 1,
    code_expires_in_days: int | None = None,
    assigned_user_id: int | None = None,
    assigned_email: str | None = None,
    notes: str | None = None,
    kind: str = "issued",
) -> AccessCode:
    if duration_days <= 0:
        raise AccessCodeError("Duration must be at least 1 day.")
    if redemption_limit <= 0:
        raise AccessCodeError("Redemption limit must be at least 1.")

    code_expires_at = None
    if code_expires_in_days is not None:
        code_expires_at = utcnow() + dt.timedelta(days=code_expires_in_days)

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
        assigned_email=(assigned_email.strip().lower() or None) if assigned_email else None,
        created_by_user_id=admin.id,
        notes=notes,
        kind=kind,
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
                "assigned_email": assigned_email,
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
    access_code.revoked_at = utcnow()
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

    if user.role == "superadmin":
        raise AccessCodeError("Super Admins have unlimited access -- there's nothing to extend.")
    if additional_days <= 0:
        raise AccessCodeError("Extension must be at least 1 day.")

    grant = current_grant(db, user)
    if grant is None:
        raise AccessCodeError("This user has no grant to extend -- generate an access code for them instead.")

    now = utcnow()
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
    if user.role == "superadmin":
        raise AccessCodeError("Super Admins have unlimited access -- demote the account first to limit it.")
    grant = current_grant(db, user)
    if grant is None:
        raise AccessCodeError("This user has no active grant to revoke.")

    grant.status = "revoked"
    grant.revoked_at = utcnow()
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


def issue_signup_trial(db: Session, user: User, *, duration_days: int) -> AccessGrant:
    """A brand-new account's one-time taste of full access, no code needed.

    Goes through the exact same create-then-redeem path a real code does,
    rather than hand-building an AccessGrant, so it leaves the identical
    trail (an AccessCode, an AccessGrant, an AccessRedemption, two audit-log
    rows) that the rest of the admin tooling already knows how to display --
    a superadmin looking at this user's access history sees an ordinary
    single-use code, not a special case. Self-issued: the new account is
    both the "admin" who cuts the code and the redeemer, since there is no
    human actor at signup to attribute it to.

    A second registration can never call this twice for the same account --
    the email uniqueness check in routes_auth.py's register() already
    prevents that -- so there is no risk of stacking trials.
    """

    code = create_access_code(
        db,
        user,
        duration_days=duration_days,
        redemption_limit=1,
        assigned_user_id=user.id,
        assigned_email=user.email,
        notes="Automatic first-signup trial",
        kind="trial",
    )
    return redeem_access_code(db, user, code.code)


def _log_redeem_failure(db: Session, user: User, reason: str, code: AccessCode | None = None) -> None:
    """A failed redemption attempt is still activity worth seeing -- a string
    of "code doesn't exist"/"already redeemed" rows against one account is
    exactly the pattern that'd never show up in the all-successful log
    access_code.redeemed alone keeps. Committed on its own rather than left
    pending, since the caller raises right after this and never reaches its
    own commit."""

    db.add(
        AuditLog(
            actor_user_id=user.id,
            actor_email=user.email,
            action="access_code.redeem_failed",
            target_user_id=user.id,
            detail={"reason": reason, **({"access_code_id": code.id} if code is not None else {})},
        )
    )
    db.commit()


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
        _log_redeem_failure(db, user, "code doesn't exist")
        raise AccessCodeError("That code doesn't exist.")

    now = utcnow()

    if code.status == "revoked":
        _log_redeem_failure(db, user, "revoked", code)
        raise AccessCodeError("This code has been revoked.")
    if code.code_expires_at is not None and now > code.code_expires_at:
        _log_redeem_failure(db, user, "expired", code)
        raise AccessCodeError("This code has expired.")
    if code.assigned_user_id is not None and code.assigned_user_id != user.id:
        _log_redeem_failure(db, user, "assigned to a different account", code)
        raise AccessCodeError("This code is assigned to a different account.")

    duplicate = db.execute(
        select(AccessRedemption).where(
            AccessRedemption.access_code_id == code.id, AccessRedemption.user_id == user.id
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        _log_redeem_failure(db, user, "already redeemed by this account", code)
        raise AccessCodeError("You've already redeemed this code.")

    result = db.execute(
        update(AccessCode)
        .where(AccessCode.id == code.id, AccessCode.redemption_count < AccessCode.redemption_limit)
        .values(redemption_count=AccessCode.redemption_count + 1)
    )
    if result.rowcount == 0:
        _log_redeem_failure(db, user, "fully redeemed", code)
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

    if code.kind == "issued":
        reward_referrer_for_first_payment(db, user)
    return grant


def generate_referral_code_string() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(_REFERRAL_CODE_LENGTH))


def ensure_referral_code(db: Session, user: User) -> str:
    """This account's own referral code, generating and persisting one on
    first use if it doesn't have one yet.

    Generation isn't part of the column's default because a default only
    fires on insert -- it can't backfill a row that already existed before
    this feature did. migrate.py's one-time backfill covers every row at
    startup; this is the lazy fallback for the (practically impossible)
    case of a row that somehow slipped past it, and the path a brand-new
    registration uses to get its first code.
    """

    if user.referral_code:
        return user.referral_code

    for _ in range(5):
        candidate = generate_referral_code_string()
        if db.execute(select(User).where(User.referral_code == candidate)).scalar_one_or_none() is None:
            break
    else:
        raise AccessCodeError("Could not generate a unique referral code -- try again.")

    user.referral_code = candidate
    db.commit()
    db.refresh(user)
    return candidate


def _grant_bonus_days(db: Session, beneficiary: User, *, bonus_days: int, role: str) -> None:
    """Adds ``bonus_days`` to ``beneficiary``'s access through the same
    create-then-redeem path as ``issue_signup_trial``, so it stacks on
    whatever the account already has (see ``redeem_access_code``'s own
    stacking logic) rather than overwriting it."""

    code = create_access_code(
        db,
        beneficiary,
        duration_days=bonus_days,
        redemption_limit=1,
        assigned_user_id=beneficiary.id,
        assigned_email=beneficiary.email,
        notes=f"Referral bonus ({role})",
        kind="referral",
    )
    redeem_access_code(db, beneficiary, code.code)


def grant_referee_bonus(db: Session, *, referee: User, referrer: User, bonus_days: int) -> None:
    """The new account's half of a referral: ``bonus_days`` on top of its
    trial, granted when its free access starts (email confirmed). The
    referrer's half waits for this account's first payment -- see
    ``reward_referrer_for_first_payment``."""

    _grant_bonus_days(db, referee, bonus_days=bonus_days, role="referee")
    db.add(
        AuditLog(
            actor_user_id=referee.id,
            actor_email=referee.email,
            action="referral.referee_bonus_granted",
            target_user_id=referrer.id,
            detail={"bonus_days": bonus_days, "referrer_email": referrer.email, "referee_email": referee.email},
        )
    )
    db.commit()


def reward_referrer_for_first_payment(db: Session, referee: User) -> bool:
    """Pays the referrer's bonus the first time a referred account redeems a
    paid ("issued") code. Returns whether a bonus was granted.

    The bonus used to land at signup, for both sides, with no limit -- so
    one person could register throwaway accounts with their own code and
    never pay. Tying it to the referee's first payment rewards bringing in
    a customer, not an email address, and the monthly cap
    (``referral_monthly_cap``) bounds it even then. Runs once per referee:
    a capped or skipped first payment is final, later ones never retry.
    """

    if referee.referred_by_user_id is None or referee.referral_rewarded_at is not None:
        return False
    if not app_settings.get_value(db, "referral_enabled"):
        return False

    paid_redemptions = db.execute(
        select(func.count(AccessRedemption.id))
        .join(AccessCode, AccessCode.id == AccessRedemption.access_code_id)
        .where(AccessRedemption.user_id == referee.id, AccessCode.kind == "issued")
    ).scalar_one()
    if paid_redemptions != 1:
        return False

    referrer = db.get(User, referee.referred_by_user_id)
    if referrer is None or referrer.status == "suspended":
        return False

    now = utcnow()
    cap = int(app_settings.get_value(db, "referral_monthly_cap") or 0)
    rewarded_recently = db.execute(
        select(func.count(User.id)).where(
            User.referred_by_user_id == referrer.id,
            User.referral_rewarded_at.is_not(None),
            User.referral_rewarded_at >= now - dt.timedelta(days=30),
        )
    ).scalar_one()
    if rewarded_recently >= cap:
        db.add(
            AuditLog(
                actor_user_id=referee.id,
                actor_email=referee.email,
                action="referral.bonus_capped",
                target_user_id=referrer.id,
                detail={"cap": cap, "referrer_email": referrer.email, "referee_email": referee.email},
            )
        )
        db.commit()
        return False

    bonus_days = int(app_settings.get_value(db, "referral_bonus_days") or 0)
    if bonus_days <= 0:
        return False

    referee.referral_rewarded_at = now
    db.commit()
    _grant_bonus_days(db, referrer, bonus_days=bonus_days, role="referrer")
    db.add(
        AuditLog(
            actor_user_id=referee.id,
            actor_email=referee.email,
            action="referral.bonus_granted",
            target_user_id=referrer.id,
            detail={"bonus_days": bonus_days, "referrer_email": referrer.email, "referee_email": referee.email},
        )
    )
    db.commit()
    return True


def referral_stats(db: Session, user: User) -> dict[str, int]:
    """How this account's referrals are doing, for the Profile page."""

    joined = db.execute(select(func.count(User.id)).where(User.referred_by_user_id == user.id)).scalar_one()
    paid = db.execute(
        select(func.count(User.id)).where(User.referred_by_user_id == user.id, User.referral_rewarded_at.is_not(None))
    ).scalar_one()
    return {"joined": int(joined), "paid": int(paid)}
