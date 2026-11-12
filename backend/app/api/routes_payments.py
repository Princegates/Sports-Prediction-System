"""Paying for access through Hubtel, alongside (not instead of) admin-issued
codes -- those stay the fallback for a payment that doesn't go through.

Member routes need only a login, not live access: the people who need to pay
are exactly the ones whose access is missing or lapsed. The callback is public
by necessity (Hubtel calls it), so it trusts nothing in its body: it names a
payment, and the payment's real state is then asked of Hubtel directly.
"""

from __future__ import annotations

import json

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

    _record_callback(db, payment, body)
    try:
        service.reconcile(db, payment, force=True)
    except hubtel.HubtelError as exc:
        raise HTTPException(status_code=503, detail="Couldn't confirm the payment yet.") from exc
    except AccessCodeError as exc:
        logger.error("Payment %s confirmed but access couldn't be granted: %s", reference, exc)
        raise HTTPException(status_code=500, detail="Couldn't record the payment yet.") from exc
    return {"ok": True}


_CALLBACK_KEEP_CHARS = 6000


def _record_callback(db: Session, payment: Payment, body: dict[str, Any]) -> None:
    """Keep exactly what Hubtel sent, for debugging with them (Activity Log,
    "payment callback received from Hubtel"). Only for payments we know -- the
    endpoint is public, so unknown references must not be able to fill the
    log. Never trusted for anything; purely a record."""

    raw = json.dumps(body, default=str)
    kept = body if len(raw) <= _CALLBACK_KEEP_CHARS else {"truncated": raw[:_CALLBACK_KEEP_CHARS]}
    service._audit(db, None, "payment.callback_received", payment, {"body": kept})
    db.commit()


def _product_label(payment: Payment) -> str:
    if payment.product == products.ACCESS:
        days = payment.access_days
        return f"Access, {days} day{'s' if days != 1 else ''}"
    product = products.BY_KEY.get(payment.product)
    return product.label if product else payment.product


def _payment_out(payment: Payment, email: str | None) -> AdminPaymentOut:
    return AdminPaymentOut(
        id=payment.id, user_id=payment.user_id, user_email=email, reference=payment.client_reference,
        amount=payment.amount, access_days=payment.access_days, status=payment.status,
        created_at=payment.created_at, completed_at=payment.completed_at,
        product=payment.product, product_label=_product_label(payment),
        last_checked_at=payment.last_checked_at,
        provider_detail=payment.provider_detail if isinstance(payment.provider_detail, dict) else None,
    )


@admin_router.get("", response_model=list[AdminPaymentOut])
def list_payments(limit: int = 200, status: str | None = None, db: Session = Depends(get_db)) -> list[AdminPaymentOut]:
    """Newest first -- for spotting a payment stuck short of "completed"
    (press "Check with Hubtel" on it, or issue a code by hand)."""

    limit = max(1, min(limit, 500))
    query = select(Payment).order_by(Payment.created_at.desc()).limit(limit)
    if status:
        query = query.where(Payment.status == status)
    rows = db.execute(query).scalars().all()
    emails = {u.id: u.email for u in db.execute(select(User).where(User.id.in_({p.user_id for p in rows if p.user_id}))).scalars()}
    return [_payment_out(p, emails.get(p.user_id)) for p in rows]


@admin_router.post("/{payment_id}/recheck", response_model=AdminPaymentOut)
def recheck_payment(
    payment_id: int, admin: User = Depends(require_superadmin), db: Session = Depends(get_db)
) -> AdminPaymentOut:
    """Asks Hubtel again about a payment still waiting, and grants whatever it
    was for if Hubtel now says it was paid -- exactly the rules a member's own
    status poll follows (a payment that isn't pending is left alone). For a
    payment whose callback never arrived, or while testing the connection."""

    # Hubtel allows only a handful of status checks a minute.
    enforce_key(f"payment_recheck-admin:{admin.id}", 10, 600)
    payment = db.get(Payment, payment_id)
    if payment is None:
        raise HTTPException(status_code=404, detail="Payment not found.")

    before = payment.status
    try:
        payment = service.reconcile(db, payment, force=True)
    except hubtel.HubtelError as exc:
        raise HTTPException(status_code=502, detail=f"Couldn't get an answer from Hubtel: {exc}") from exc
    except AccessCodeError as exc:
        logger.error("Payment %s confirmed but access couldn't be granted: %s", payment_id, exc)
        raise HTTPException(status_code=500, detail="Hubtel says it was paid, but granting it failed. Issue a code by hand.") from exc

    if before == "pending":  # only when Hubtel was actually asked
        service._audit(db, admin, "payment.rechecked_by_admin", payment, {"before": before, "after": payment.status})
        db.commit()
    email = db.get(User, payment.user_id).email if payment.user_id else None
    return _payment_out(payment, email)
