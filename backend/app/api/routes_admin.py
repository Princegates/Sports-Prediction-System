from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_superadmin
from app.api.schemas import AdminOverviewOut, AdminUserOut, ApproveIn, AuditLogOut
from app.api.serializers import admin_user_to_schema
from app.db.models import AuditLog, ChatMessage, Match, Prediction, User

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_superadmin)])


def _record(
    db: Session,
    actor: User,
    action: str,
    target: User | None = None,
    detail: dict | None = None,
) -> None:
    """Append to the audit log. ``actor_email`` is denormalized on purpose:
    an audit entry needs to stay readable after the acting account is renamed
    or deleted, which a foreign key alone won't give you."""

    db.add(
        AuditLog(
            actor_user_id=actor.id,
            actor_email=actor.email,
            action=action,
            target_user_id=target.id if target else None,
            detail=detail,
        )
    )


@router.get("/overview", response_model=AdminOverviewOut)
def overview(db: Session = Depends(get_db)) -> AdminOverviewOut:
    """Headline counts for the admin dashboard -- above all how many accounts
    are sitting in the approval queue, since nobody can use the system until
    someone acts on those."""

    def count_users(**filters) -> int:
        stmt = select(func.count()).select_from(User)
        for column, value in filters.items():
            stmt = stmt.where(getattr(User, column) == value)
        return db.execute(stmt).scalar() or 0

    now = dt.datetime.utcnow()
    return AdminOverviewOut(
        pending_users=count_users(status="pending"),
        active_users=count_users(status="active"),
        suspended_users=count_users(status="suspended"),
        superadmins=count_users(role="superadmin"),
        total_users=count_users(),
        matches_analyzed=db.execute(
            select(func.count()).select_from(Match).where(Match.home_score.is_not(None))
        ).scalar() or 0,
        predictions_generated=db.execute(select(func.count()).select_from(Prediction)).scalar() or 0,
        upcoming_fixtures=db.execute(
            select(func.count()).select_from(Match).where(Match.date >= now)
        ).scalar() or 0,
        chat_messages=db.execute(select(func.count()).select_from(ChatMessage)).scalar() or 0,
    )


@router.get("/users", response_model=list[AdminUserOut])
def list_users(status: str | None = None, db: Session = Depends(get_db)) -> list[AdminUserOut]:
    stmt = select(User)
    if status:
        stmt = stmt.where(User.status == status)
    users = db.execute(stmt.order_by(User.created_at.desc())).scalars()
    return [admin_user_to_schema(u) for u in users]


@router.get("/audit-log", response_model=list[AuditLogOut])
def audit_log(limit: int = 100, db: Session = Depends(get_db)) -> list[AuditLogOut]:
    limit = max(1, min(limit, 500))
    rows = db.execute(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)).scalars()
    return [
        AuditLogOut(
            id=row.id,
            actor_email=row.actor_email,
            action=row.action,
            target_user_id=row.target_user_id,
            detail=row.detail,
            created_at=row.created_at,
        )
        for row in rows
    ]


def _get_target_user(user_id: int, db: Session) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")
    return user


@router.post("/users/{user_id}/approve", response_model=AdminUserOut)
def approve_user(
    user_id: int,
    payload: ApproveIn,
    admin: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
) -> AdminUserOut:
    """Manual payment confirmation + account approval (spec: Hubtel
    integration comes later -- for now a superadmin reviews the user's
    submitted ``payment_reference`` out of band and confirms it here).

    TODO(hubtel): once configured, this is where an automatic call to
    Hubtel's Transaction Status API would verify ``payment_reference``
    before allowing approval, instead of trusting the admin's manual check.
    """

    target = _get_target_user(user_id, db)
    previous_status = target.status
    if payload.payment_reference:
        target.payment_reference = payload.payment_reference
    target.status = "active"
    target.approved_by_user_id = admin.id
    target.approved_at = dt.datetime.utcnow()
    _record(
        db,
        admin,
        "user.approved",
        target,
        {"from_status": previous_status, "payment_reference": target.payment_reference},
    )
    db.commit()
    db.refresh(target)
    return admin_user_to_schema(target)


@router.post("/users/{user_id}/suspend", response_model=AdminUserOut)
def suspend_user(user_id: int, admin: User = Depends(require_superadmin), db: Session = Depends(get_db)) -> AdminUserOut:
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="You cannot suspend your own account")
    target = _get_target_user(user_id, db)
    previous_status = target.status
    target.status = "suspended"
    _record(db, admin, "user.suspended", target, {"from_status": previous_status})
    db.commit()
    db.refresh(target)
    return admin_user_to_schema(target)


@router.post("/users/{user_id}/reinstate", response_model=AdminUserOut)
def reinstate_user(user_id: int, admin: User = Depends(require_superadmin), db: Session = Depends(get_db)) -> AdminUserOut:
    """Undo a suspension. Without this, suspending an account was a one-way
    door -- the only way back was editing the database by hand."""

    target = _get_target_user(user_id, db)
    if target.status == "active":
        raise HTTPException(status_code=400, detail="That account is already active")
    previous_status = target.status
    target.status = "active"
    target.approved_by_user_id = admin.id
    target.approved_at = dt.datetime.utcnow()
    _record(db, admin, "user.reinstated", target, {"from_status": previous_status})
    db.commit()
    db.refresh(target)
    return admin_user_to_schema(target)


@router.post("/users/{user_id}/promote", response_model=AdminUserOut)
def promote_user(user_id: int, admin: User = Depends(require_superadmin), db: Session = Depends(get_db)) -> AdminUserOut:
    target = _get_target_user(user_id, db)
    target.role = "superadmin"
    _record(db, admin, "user.promoted", target, {"to_role": "superadmin"})
    db.commit()
    db.refresh(target)
    return admin_user_to_schema(target)


@router.post("/users/{user_id}/demote", response_model=AdminUserOut)
def demote_user(user_id: int, admin: User = Depends(require_superadmin), db: Session = Depends(get_db)) -> AdminUserOut:
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="You cannot demote your own account")
    target = _get_target_user(user_id, db)

    # Belt and braces: leaving zero superadmins means nobody can approve
    # registrations again without direct database access. Over this endpoint
    # the self-demotion guard above already makes that unreachable (the caller
    # is a superadmin, so demoting *someone else* always leaves the caller),
    # but that argument depends on both guards staying in place, and the cost
    # of stating the invariant directly is one query.
    remaining_admins = db.execute(
        select(func.count()).select_from(User).where(User.role == "superadmin", User.id != target.id)
    ).scalar() or 0
    if target.role == "superadmin" and remaining_admins == 0:
        raise HTTPException(
            status_code=400,
            detail="This is the only superadmin -- promote another account before demoting this one.",
        )

    target.role = "user"
    _record(db, admin, "user.demoted", target, {"to_role": "user"})
    db.commit()
    db.refresh(target)
    return admin_user_to_schema(target)
