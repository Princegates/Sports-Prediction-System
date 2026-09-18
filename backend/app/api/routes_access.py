"""Redeeming an access code and checking where an account's access stands.

Separate from routes_admin.py's code-management endpoints: those need
``require_superadmin``; these need only a logged-in account, since redeeming
a code and checking your own status is exactly what an account *without*
current access still needs to be able to do.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.access import AccessCodeError, current_grant, redeem_access_code
from app.api.deps import get_current_user, get_db
from app.api.rate_limit import enforce
from app.api.schemas import AccessGrantOut, AccessRedeemIn, AccessStatusOut
from app.api.serializers import access_grant_to_schema
from app.config import get_settings
from app.db.models import User

router = APIRouter(prefix="/api/access", tags=["access"])


@router.post("/redeem", response_model=AccessGrantOut)
def redeem(
    payload: AccessRedeemIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AccessGrantOut:
    settings = get_settings()
    enforce(
        request,
        "access_redeem",
        settings.access_redeem_rate_limit_attempts,
        settings.access_redeem_rate_limit_window_seconds,
    )

    try:
        grant = redeem_access_code(db, user, payload.code)
    except AccessCodeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return access_grant_to_schema(grant)


@router.get("/status", response_model=AccessStatusOut)
def access_status(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> AccessStatusOut:
    grant = current_grant(db, user)
    if grant is None:
        return AccessStatusOut(has_access=False, status="none", expires_at=None)

    if grant.expires_at <= dt.datetime.utcnow():
        return AccessStatusOut(has_access=False, status="expired", expires_at=grant.expires_at)
    return AccessStatusOut(has_access=True, status="active", expires_at=grant.expires_at)
