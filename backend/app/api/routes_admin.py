from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_superadmin
from app.api.schemas import AdminUserOut, ApproveIn
from app.api.serializers import admin_user_to_schema
from app.db.models import User

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_superadmin)])


@router.get("/users", response_model=list[AdminUserOut])
def list_users(status: str | None = None, db: Session = Depends(get_db)) -> list[AdminUserOut]:
    stmt = select(User)
    if status:
        stmt = stmt.where(User.status == status)
    users = db.execute(stmt.order_by(User.created_at.desc())).scalars()
    return [admin_user_to_schema(u) for u in users]


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
    if payload.payment_reference:
        target.payment_reference = payload.payment_reference
    target.status = "active"
    target.approved_by_user_id = admin.id
    target.approved_at = dt.datetime.utcnow()
    db.commit()
    db.refresh(target)
    return admin_user_to_schema(target)


@router.post("/users/{user_id}/suspend", response_model=AdminUserOut)
def suspend_user(user_id: int, admin: User = Depends(require_superadmin), db: Session = Depends(get_db)) -> AdminUserOut:
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="You cannot suspend your own account")
    target = _get_target_user(user_id, db)
    target.status = "suspended"
    db.commit()
    db.refresh(target)
    return admin_user_to_schema(target)


@router.post("/users/{user_id}/promote", response_model=AdminUserOut)
def promote_user(user_id: int, db: Session = Depends(get_db)) -> AdminUserOut:
    target = _get_target_user(user_id, db)
    target.role = "superadmin"
    db.commit()
    db.refresh(target)
    return admin_user_to_schema(target)


@router.post("/users/{user_id}/demote", response_model=AdminUserOut)
def demote_user(user_id: int, admin: User = Depends(require_superadmin), db: Session = Depends(get_db)) -> AdminUserOut:
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="You cannot demote your own account")
    target = _get_target_user(user_id, db)
    target.role = "user"
    db.commit()
    db.refresh(target)
    return admin_user_to_schema(target)
