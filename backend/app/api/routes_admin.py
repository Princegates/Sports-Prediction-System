from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.access import (
    AccessCodeError,
    create_access_code,
    extend_grant,
    revoke_access_code,
    revoke_current_grant,
)
from app.api.deps import get_db, require_superadmin
from app.api.schemas import (
    AccessCodeCreatedOut,
    AccessCodeCreateIn,
    AccessCodeOut,
    AdminOverviewOut,
    AdminSettingsIn,
    AdminSettingsOut,
    AdminUserOut,
    AuditLogOut,
    ExtendGrantIn,
    RevokeCodeIn,
    RevokeGrantIn,
)
from app.api.serializers import access_code_to_schema, admin_user_to_schema
from app.db.models import AccessCode, AccessGrant, AdminSettings, AuditLog, ChatMessage, Match, Prediction, User

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
    """Headline counts for the admin dashboard -- above all how many active
    accounts have no live access grant, since that's the new attention queue
    now that every account can log in immediately after registering."""

    def count_users(**filters) -> int:
        stmt = select(func.count()).select_from(User)
        for column, value in filters.items():
            stmt = stmt.where(getattr(User, column) == value)
        return db.execute(stmt).scalar() or 0

    now = dt.datetime.utcnow()
    active_users = count_users(status="active")
    active_grants = db.execute(
        select(func.count(func.distinct(AccessGrant.user_id))).where(
            AccessGrant.status == "active", AccessGrant.expires_at > now
        )
    ).scalar() or 0
    return AdminOverviewOut(
        active_users=active_users,
        suspended_users=count_users(status="suspended"),
        superadmins=count_users(role="superadmin"),
        total_users=count_users(),
        users_without_access=max(active_users - active_grants, 0),
        active_access_grants=active_grants,
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
    return [admin_user_to_schema(u, db) for u in users]


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
    return admin_user_to_schema(target, db)


@router.post("/users/{user_id}/reinstate", response_model=AdminUserOut)
def reinstate_user(user_id: int, admin: User = Depends(require_superadmin), db: Session = Depends(get_db)) -> AdminUserOut:
    """Undo a suspension. Without this, suspending an account was a one-way
    door -- the only way back was editing the database by hand."""

    target = _get_target_user(user_id, db)
    if target.status == "active":
        raise HTTPException(status_code=400, detail="That account is already active")
    previous_status = target.status
    target.status = "active"
    _record(db, admin, "user.reinstated", target, {"from_status": previous_status})
    db.commit()
    db.refresh(target)
    return admin_user_to_schema(target, db)


@router.post("/users/{user_id}/promote", response_model=AdminUserOut)
def promote_user(user_id: int, admin: User = Depends(require_superadmin), db: Session = Depends(get_db)) -> AdminUserOut:
    target = _get_target_user(user_id, db)
    target.role = "superadmin"
    _record(db, admin, "user.promoted", target, {"to_role": "superadmin"})
    db.commit()
    db.refresh(target)
    return admin_user_to_schema(target, db)


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
    return admin_user_to_schema(target, db)


# --- Access codes -----------------------------------------------------------


@router.post("/access-codes", response_model=AccessCodeCreatedOut)
def create_code(
    payload: AccessCodeCreateIn,
    admin: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
) -> AccessCodeCreatedOut:
    assigned_user_id = None
    if payload.assigned_user_email:
        target = db.execute(
            select(User).where(User.email == payload.assigned_user_email.strip().lower())
        ).scalar_one_or_none()
        if target is None:
            raise HTTPException(status_code=404, detail="No account with that email")
        assigned_user_id = target.id

    try:
        code = create_access_code(
            db,
            admin,
            duration_days=payload.duration_days,
            redemption_limit=payload.redemption_limit,
            code_expires_in_days=payload.code_expires_in_days,
            assigned_user_id=assigned_user_id,
            notes=payload.notes,
        )
    except AccessCodeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return AccessCodeCreatedOut(**access_code_to_schema(code, reveal_full=True).model_dump())


@router.get("/access-codes", response_model=list[AccessCodeOut])
def list_access_codes(db: Session = Depends(get_db)) -> list[AccessCodeOut]:
    codes = db.execute(select(AccessCode).order_by(AccessCode.created_at.desc())).scalars()
    return [access_code_to_schema(c) for c in codes]


@router.post("/access-codes/{code_id}/revoke", response_model=AccessCodeOut)
def revoke_code(
    code_id: int,
    payload: RevokeCodeIn,
    admin: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
) -> AccessCodeOut:
    code = db.get(AccessCode, code_id)
    if code is None:
        raise HTTPException(status_code=404, detail=f"Access code {code_id} not found")
    try:
        code = revoke_access_code(db, admin, code, payload.reason)
    except AccessCodeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return access_code_to_schema(code)


@router.post("/users/{user_id}/access/extend", response_model=AdminUserOut)
def extend_user_access(
    user_id: int,
    payload: ExtendGrantIn,
    admin: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
) -> AdminUserOut:
    target = _get_target_user(user_id, db)
    try:
        extend_grant(db, admin, target, payload.additional_days)
    except AccessCodeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return admin_user_to_schema(target, db)


@router.post("/users/{user_id}/access/revoke", response_model=AdminUserOut)
def revoke_user_access(
    user_id: int,
    payload: RevokeGrantIn,
    admin: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
) -> AdminUserOut:
    target = _get_target_user(user_id, db)
    try:
        revoke_current_grant(db, admin, target, payload.reason)
    except AccessCodeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return admin_user_to_schema(target, db)


# --- Platform settings -------------------------------------------------------


def _get_or_create_settings(db: Session) -> AdminSettings:
    settings = db.get(AdminSettings, 1)
    if settings is None:
        settings = AdminSettings(id=1)
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


@router.get("/settings", response_model=AdminSettingsOut)
def get_admin_settings(db: Session = Depends(get_db)) -> AdminSettingsOut:
    settings = _get_or_create_settings(db)
    return AdminSettingsOut(
        default_duration_days=settings.default_duration_days,
        default_redemption_limit=settings.default_redemption_limit,
        updated_at=settings.updated_at,
    )


@router.patch("/settings", response_model=AdminSettingsOut)
def update_admin_settings(
    payload: AdminSettingsIn,
    admin: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
) -> AdminSettingsOut:
    if payload.default_duration_days <= 0 or payload.default_redemption_limit <= 0:
        raise HTTPException(status_code=400, detail="Values must be at least 1")

    settings = _get_or_create_settings(db)
    settings.default_duration_days = payload.default_duration_days
    settings.default_redemption_limit = payload.default_redemption_limit
    settings.updated_by_user_id = admin.id
    settings.updated_at = dt.datetime.utcnow()
    _record(
        db,
        admin,
        "admin_settings.updated",
        detail={
            "default_duration_days": payload.default_duration_days,
            "default_redemption_limit": payload.default_redemption_limit,
        },
    )
    db.commit()
    db.refresh(settings)
    return AdminSettingsOut(
        default_duration_days=settings.default_duration_days,
        default_redemption_limit=settings.default_redemption_limit,
        updated_at=settings.updated_at,
    )
