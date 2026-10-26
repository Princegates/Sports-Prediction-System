"""Starting a payment and turning a confirmed one into access.

The rule everything here is built around: a payment grants access only when
Hubtel itself says it was paid, asked server to server (``reconcile``) --
never because a request claimed it. The callback route and the member's own
status polling both go through ``reconcile``, so a lost or forged callback
can neither withhold nor invent access.

Fulfilment reuses the access-code path (``create_access_code`` then
``redeem_access_code``, the same as ``issue_signup_trial``), so a Hubtel
payment leaves the ordinary AccessCode / AccessGrant / audit trail behind and
counts as a paid ("issued") redemption for referral rewards. An admin-issued
code stays the fallback for a payment that doesn't go through.
"""

from __future__ import annotations

import datetime as dt
import secrets

from sqlalchemy import update
from sqlalchemy.orm import Session

from app import app_settings
from app.access import AccessCodeError, create_access_code, redeem_access_code
from app.api import rate_limit
from app.db.models import AuditLog, Payment, User
from app.payments import hubtel
from app.timeutil import utcnow

# Hubtel rate-limits these endpoints (~5/min), so a member's polling can't be
# allowed to turn into a status call per poll.
STATUS_CHECK_MIN_INTERVAL = dt.timedelta(seconds=15)
GLOBAL_STATUS_CHECKS_PER_MINUTE = 4


class PaymentError(Exception):
    """Something the member should be told, verbatim."""


def _price(db: Session) -> tuple[float, int, str]:
    values = app_settings.all_values(db)
    return (
        float(values.get("price_amount") or 0),
        int(values.get("price_days") or 0),
        str(values.get("price_currency") or ""),
    )


def options(db: Session) -> dict:
    """What the Access page needs to decide whether to offer "Pay now"."""

    amount, days, currency = _price(db)
    config = hubtel.resolve_config(db)
    enabled = bool(app_settings.get_value(db, "hubtel_enabled")) and config.configured and amount > 0 and days > 0
    return {"enabled": enabled, "amount": amount, "days": days, "currency": currency}


def _new_reference() -> str:
    # "SI" + 30 hex characters = 32, the most Hubtel accepts.
    return "SI" + secrets.token_hex(15)


def _audit(db: Session, user: User | None, action: str, payment: Payment, detail: dict | None = None) -> None:
    db.add(
        AuditLog(
            actor_user_id=user.id if user else None,
            actor_email=user.email if user else None,
            action=action,
            target_user_id=payment.user_id,
            detail={"payment_id": payment.id, "reference": payment.client_reference, **(detail or {})},
        )
    )


def start_checkout(db: Session, user: User) -> Payment:
    opts = options(db)
    if not opts["enabled"]:
        raise PaymentError("Online payment isn't available right now -- contact an admin for an access code.")

    config = hubtel.resolve_config(db)
    site = str(app_settings.get_value(db, "public_site_url") or "").rstrip("/")
    if not site:
        raise PaymentError("Online payment isn't fully set up yet -- contact an admin for an access code.")
    site_name = str(app_settings.get_value(db, "site_name") or "Socca Intelligence")

    payment = Payment(
        user_id=user.id,
        client_reference=_new_reference(),
        amount=opts["amount"],
        access_days=opts["days"],
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)

    reference = payment.client_reference
    try:
        result = hubtel.create_checkout(
            config,
            amount=payment.amount,
            description=f"{payment.access_days}-day access to {site_name}",
            client_reference=reference,
            callback_url=f"{config.public_api_url}/api/payments/hubtel/callback",
            return_url=f"{site}/app/access?payment={reference}",
            cancellation_url=f"{site}/app/access?payment={reference}&cancelled=1",
        )
    except hubtel.HubtelError as exc:
        payment.status = "failed"
        payment.provider_detail = {"error": str(exc)}
        db.commit()
        raise PaymentError(str(exc)) from exc

    payment.checkout_id = result.checkout_id
    payment.checkout_url = result.checkout_url
    db.commit()
    db.refresh(payment)
    return payment


def _fulfil(db: Session, payment: Payment) -> None:
    """Cuts and redeems the access code for a confirmed payment. Exactly one
    caller wins the pending -> fulfilling claim, so two callbacks (Hubtel
    retries) racing each other can't grant twice."""

    claimed = db.execute(
        update(Payment).where(Payment.id == payment.id, Payment.status == "pending").values(status="fulfilling")
    )
    db.commit()
    if claimed.rowcount == 0:
        return

    user = db.get(User, payment.user_id) if payment.user_id is not None else None
    if user is None:
        payment.status = "failed"
        payment.provider_detail = {**(payment.provider_detail or {}), "error": "account no longer exists"}
        db.commit()
        return

    try:
        code = create_access_code(
            db,
            user,
            duration_days=payment.access_days,
            redemption_limit=1,
            assigned_user_id=user.id,
            assigned_email=user.email,
            notes=f"Hubtel payment {payment.client_reference}",
            kind="issued",
        )
        redeem_access_code(db, user, code.code)
    except AccessCodeError:
        # Paid but not granted: back to pending so the next callback or poll
        # retries. The admin-issued code is the manual way out if it sticks.
        db.rollback()
        payment = db.get(Payment, payment.id)
        payment.status = "pending"
        db.commit()
        raise

    payment = db.get(Payment, payment.id)
    payment.status = "completed"
    payment.completed_at = utcnow()
    payment.access_code_id = code.id
    _audit(db, user, "payment.completed", payment, {"amount": payment.amount, "access_days": payment.access_days})
    db.commit()


def reconcile(db: Session, payment: Payment, *, force: bool = False) -> Payment:
    """Brings a pending payment up to date with what Hubtel says, granting
    access if it was paid. ``force`` skips the polling gate (used by the
    callback, which should verify immediately). Raises HubtelError when
    Hubtel can't be asked -- the payment is left untouched."""

    if payment.status != "pending":
        return payment

    now = utcnow()
    if not force:
        if payment.last_checked_at is not None and now - payment.last_checked_at < STATUS_CHECK_MIN_INTERVAL:
            return payment
        allowed, _ = rate_limit.check("hubtel:status", GLOBAL_STATUS_CHECKS_PER_MINUTE, 60)
        if not allowed:
            return payment
    else:
        rate_limit.check("hubtel:status", GLOBAL_STATUS_CHECKS_PER_MINUTE, 60)

    config = hubtel.resolve_config(db)
    result = hubtel.check_status(config, payment.client_reference)

    payment.last_checked_at = now
    payment.provider_detail = result.raw
    db.commit()

    if result.state == "paid":
        if result.amount is not None and result.amount + 0.005 < payment.amount:
            payment.status = "failed"
            _audit(db, None, "payment.amount_mismatch", payment, {"expected": payment.amount, "paid": result.amount})
            db.commit()
            return payment
        _fulfil(db, payment)
    elif result.state == "unpaid":
        payment.status = "cancelled"
        _audit(db, None, "payment.cancelled", payment)
        db.commit()

    db.refresh(payment)
    return payment
