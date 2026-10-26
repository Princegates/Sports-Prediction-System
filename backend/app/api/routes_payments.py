"""Paying for access through Hubtel, alongside (not instead of) admin-issued
codes -- those stay the fallback for a payment that doesn't go through.

Member routes need only a login, not live access: the people who need to pay
are exactly the ones whose access is missing or lapsed. The callback is public
by necessity (Hubtel calls it), so it trusts nothing in its body: it names a
payment, and the payment's real state is then asked of Hubtel directly.
"""

from __future__ import annotations

import logging

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.access import AccessCodeError, current_grant
from app.api.deps import get_current_user, get_db, require_superadmin
from app.api.rate_limit import enforce, enforce_key
from app.api.schemas import AdminPaymentOut, CheckoutIn, CheckoutOut, PaymentOptionsOut, PaymentStatusOut
from app.db.models import Payment, User
from app.payments import hubtel, products, service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/payments", tags=["payments"])
admin_router = APIRouter(prefix="/api/admin/payments", tags=["payments"], dependencies=[Depends(require_superadmin)])


@router.get("/options", response_model=PaymentOptionsOut)
def payment_options(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> PaymentOptionsOut:
    return PaymentOptionsOut(**service.options(db, user))


@router.post("/checkout", response_model=CheckoutOut)
def start_checkout(
    payload: CheckoutIn | None = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> CheckoutOut:
    # Each call creates a payment row and calls Hubtel; keyed on the account.
    enforce_key(f"payment_checkout-user:{user.id}", 5, 600)
    try:
        payment = service.start_checkout(db, user, payload.product if payload else products.ACCESS)
    except service.PaymentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return CheckoutOut(reference=payment.client_reference, checkout_url=payment.checkout_url or "")


@router.get("/{reference}/status", response_model=PaymentStatusOut)
def payment_status(
    reference: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> PaymentStatusOut:
    """What the page the member returns to from Hubtel polls. Asks Hubtel
    itself (at a bounded rate) while the payment is pending, so a lost
    callback delays access rather than withholding it."""

    payment = db.execute(select(Payment).where(Payment.client_reference == reference)).scalar_one_or_none()
    if payment is None or payment.user_id != user.id:
        raise HTTPException(status_code=404, detail="Payment not found.")

    try:
        payment = service.reconcile(db, payment)
    except hubtel.HubtelError:
        pass  # still pending as far as we know; the next poll tries again
    except AccessCodeError as exc:
        logger.error("Payment %s confirmed but access couldn't be granted: %s", reference, exc)

    expires_at = None
    if payment.status == "completed" and payment.product == products.ACCESS:
        grant = current_grant(db, user)
        expires_at = grant.expires_at if grant else None
    return PaymentStatusOut(
        reference=payment.client_reference, product=payment.product, status=payment.status, access_expires_at=expires_at
    )


@router.post("/hubtel/callback")
def hubtel_callback(
    request: Request, body: dict[str, Any] = Body(...), db: Session = Depends(get_db)
) -> dict:
    """Hubtel's payment notification. The body only identifies which payment
    to look at -- whether it was paid is confirmed with Hubtel directly, so a
    forged callback can't grant anything. Unknown references get a plain 200
    (nothing for Hubtel to retry, nothing to probe); a failure to reach Hubtel
    is a 503 so Hubtel retries, with the member's own polling as the backstop."""

    enforce(request, "hubtel_callback", 120, 60)
    reference = hubtel.callback_reference(body)
    if not reference:
        return {"ok": False}
    payment = db.execute(select(Payment).where(Payment.client_reference == reference)).scalar_one_or_none()
    if payment is None:
        logger.info("Hubtel callback for unknown reference %s", reference[:40])
        return {"ok": False}

    try:
        service.reconcile(db, payment, force=True)
    except hubtel.HubtelError as exc:
        raise HTTPException(status_code=503, detail="Couldn't confirm the payment yet.") from exc
    except AccessCodeError as exc:
        logger.error("Payment %s confirmed but access couldn't be granted: %s", reference, exc)
        raise HTTPException(status_code=500, detail="Couldn't record the payment yet.") from exc
    return {"ok": True}


@admin_router.get("", response_model=list[AdminPaymentOut])
def list_payments(limit: int = 100, db: Session = Depends(get_db)) -> list[AdminPaymentOut]:
    """Newest first -- for spotting a payment stuck short of "completed",
    which the admin can resolve by issuing a code by hand."""

    limit = max(1, min(limit, 500))
    rows = db.execute(select(Payment).order_by(Payment.created_at.desc()).limit(limit)).scalars().all()
    emails = {u.id: u.email for u in db.execute(select(User).where(User.id.in_({p.user_id for p in rows if p.user_id}))).scalars()}
    return [
        AdminPaymentOut(
            id=p.id, user_id=p.user_id, user_email=emails.get(p.user_id), reference=p.client_reference,
            amount=p.amount, access_days=p.access_days, status=p.status,
            created_at=p.created_at, completed_at=p.completed_at,
        )
        for p in rows
    ]
