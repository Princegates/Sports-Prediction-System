"""Redeeming an access code and checking where an account's access stands.

Separate from routes_admin.py's code-management endpoints: those need
``require_superadmin``; these need only a logged-in account, since redeeming
a code and checking your own status is exactly what an account *without*
current access still needs to be able to do.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.timeutil import utcnow
from app import mailer
from app.access import AccessCodeError, current_grant, redeem_access_code
from app.api.deps import get_current_user, get_db
from app.api.rate_limit import enforce_key
from app.api.schemas import AccessGrantOut, AccessRedeemIn, AccessStatusOut, MessageOut, PaymentNoticeIn
from app.api.serializers import access_grant_to_schema
from app.config import get_settings
from app.db.models import AuditLog, User

router = APIRouter(prefix="/api/access", tags=["access"])


@router.post("/redeem", response_model=AccessGrantOut)
def redeem(
    payload: AccessRedeemIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AccessGrantOut:
    settings = get_settings()
    # Keyed on the account, which can't be rotated for free the way a
    # forwarded IP header can.
    enforce_key(
        f"access_redeem-user:{user.id}",
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
    if user.role == "superadmin":
        return AccessStatusOut(has_access=True, status="unlimited")

    grant = current_grant(db, user)
    if grant is None:
        return AccessStatusOut(has_access=False, status="none")

    has_access = grant.expires_at > utcnow()
    return AccessStatusOut(
        has_access=has_access,
        status="active" if has_access else "expired",
        activated_at=grant.activated_at,
        expires_at=grant.expires_at,
    )


@router.post("/payment-notice", response_model=MessageOut)
def report_payment(
    payload: PaymentNoticeIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MessageOut:
    """"I've paid" -- tells the Super Admins straight away, with the
    reference the account gave, so issuing the code doesn't wait on someone
    noticing a WhatsApp message. Records the reference only; nothing here
    takes or verifies a payment (payment stays outside the platform)."""

    enforce_key(f"payment_notice-user:{user.id}", 3, 24 * 3600)
    reference = payload.reference.strip()
    if not reference:
        raise HTTPException(status_code=400, detail="Add the payment reference so the team can match it.")

    db.add(
        AuditLog(
            actor_user_id=user.id,
            actor_email=user.email,
            action="access.payment_reported",
            target_user_id=user.id,
            detail={"reference": reference},
        )
    )
    db.commit()

    if mailer.is_configured(db):
        admins = db.execute(select(User).where(User.role == "superadmin", User.status != "suspended")).scalars().all()
        subject, body = mailer.payment_reported_message(user.email, reference, mailer.resolve_config(db).site_url or None)
        for admin in admins:
            mailer.send_email(admin.email, subject, body, db=db)

    return MessageOut(
        message="Thanks -- the team has been told. Your code will come by email or WhatsApp once the payment is confirmed."
    )
