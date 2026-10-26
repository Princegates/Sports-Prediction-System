"""Deleting an account from the platform entirely (Admin -> Users -> Delete).

Everything that is the person's own goes with them: the account row, its
access grants and code redemptions, chat history, viewed-match history and
saved booking slips. Records that belong to the platform rather than the
person stay, but stop pointing at them:

- access codes assigned to them are revoked if still usable (an unassigned
  code would otherwise become redeemable by anyone) and the assignment and
  email are cleared; codes they created (as a former admin) are reassigned
  to the admin doing the deleting, since a code must have a creator;
- picks, settings and winning-slip posts they authored keep their content,
  without the author; payment records are kept (a financial record isn't the
  person's own to erase), just unlinked from the account;
- audit-log entries keep the action, with the link to them and their email
  removed; accounts they referred simply lose the referral link.

A superadmin can't be deleted (demote it first), nor can the caller delete
itself. The deletion itself is audited by user id and role only -- no name
or email, which would defeat the point.
"""

from __future__ import annotations

from sqlalchemy import delete, update
from sqlalchemy.orm import Session

from app.db.models import (
    AccessCode,
    AccessGrant,
    AccessRedemption,
    AdminPick,
    AppSetting,
    AuditLog,
    BookingSlip,
    ChatMessage,
    FeaturedPick,
    MatchView,
    Payment,
    User,
    WinningSlip,
)
from app.timeutil import utcnow


class AccountDeletionError(Exception):
    pass


def delete_user_account(db: Session, admin: User, target: User) -> None:
    if target.id == admin.id:
        raise AccountDeletionError("You cannot delete your own account.")
    if target.role == "superadmin":
        raise AccountDeletionError("Super Admin accounts can't be deleted -- demote the account first.")

    user_id, role = target.id, target.role
    now = utcnow()

    for model in (AccessRedemption, AccessGrant, ChatMessage, MatchView, BookingSlip):
        db.execute(delete(model).where(model.user_id == user_id))

    db.execute(
        update(AccessCode)
        .where(AccessCode.assigned_user_id == user_id, AccessCode.revoked_at.is_(None))
        .values(status="revoked", revoked_at=now, revoked_reason="Assigned account deleted")
    )
    db.execute(
        update(AccessCode)
        .where((AccessCode.assigned_user_id == user_id) | (AccessCode.assigned_email == target.email))
        .values(assigned_user_id=None, assigned_email=None)
    )
    db.execute(update(AccessCode).where(AccessCode.created_by_user_id == user_id).values(created_by_user_id=admin.id))

    db.execute(update(AdminPick).where(AdminPick.created_by_user_id == user_id).values(created_by_user_id=None))
    db.execute(update(FeaturedPick).where(FeaturedPick.created_by_user_id == user_id).values(created_by_user_id=None))
    db.execute(update(AppSetting).where(AppSetting.updated_by_user_id == user_id).values(updated_by_user_id=None))
    db.execute(update(WinningSlip).where(WinningSlip.created_by_user_id == user_id).values(created_by_user_id=None))
    db.execute(update(User).where(User.referred_by_user_id == user_id).values(referred_by_user_id=None))
    db.execute(update(Payment).where(Payment.user_id == user_id).values(user_id=None))

    db.execute(update(AuditLog).where(AuditLog.actor_user_id == user_id).values(actor_user_id=None, actor_email=None))
    db.execute(update(AuditLog).where(AuditLog.target_user_id == user_id).values(target_user_id=None))

    db.delete(target)
    db.add(
        AuditLog(
            actor_user_id=admin.id,
            actor_email=admin.email,
            action="user.deleted",
            target_user_id=None,
            detail={"user_id": user_id, "role": role},
        )
    )
    db.commit()
