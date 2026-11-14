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

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app import app_settings
from app.access import AccessCodeError, create_access_code, current_grant, redeem_access_code
from app.api import rate_limit
from app.db.models import AccessCode, AccessRedemption, AuditLog, Payment, User
from app.payments import hubtel, products
from app.timeutil import utcnow

# Hubtel rate-limits these endpoints (~5/min), so a member's polling can't be
# allowed to turn into a status call per poll.
STATUS_CHECK_MIN_INTERVAL = dt.timedelta(seconds=15)
GLOBAL_STATUS_CHECKS_PER_MINUTE = 4

# Hubtel reports "Unpaid" both for a payment that failed and for one the
# customer simply hasn't approved yet, so "Unpaid" alone must not cancel a
# payment -- a cancelled payment is never looked at again, and a late approval
# would be lost. It cancels only after this long; Hubtel's unambiguous answers
# (failed, declined, expired...) cancel at once.
UNPAID_GRACE = dt.timedelta(minutes=30)

# The background sweep (``sweep_pending``): asks Hubtel about payments whose
# callback never arrived. Gives the callback a head start, then re-asks
# unsettled payments every few minutes for up to three days.
SWEEP_MIN_AGE = dt.timedelta(minutes=2)
SWEEP_RECHECK_EVERY = dt.timedelta(minutes=2)
SWEEP_MAX_AGE = dt.timedelta(days=3)
SWEEP_BATCH = 10

# A payment is "fulfilling" only for the moment between Hubtel saying it was
# paid and the grant being written. One still there after this long was
# interrupted (a restart, a database error) and would otherwise stay paid
# without access for good, so the sweep puts it back to "pending" to be
# confirmed and granted again -- granting is safe to repeat, see ``_fulfil``.
FULFILLING_STUCK_AFTER = dt.timedelta(minutes=5)


class PaymentError(Exception):
    """Something the member should be told, verbatim."""


def _price(db: Session) -> tuple[float, int, str]:
    values = app_settings.all_values(db)
    return (
        float(values.get("price_amount") or 0),
        int(values.get("price_days") or 0),
        str(values.get("price_currency") or ""),
    )


def _gateway_ready(db: Session) -> bool:
    return bool(app_settings.get_value(db, "hubtel_enabled")) and hubtel.resolve_config(db).configured


def options(db: Session, user: User | None = None) -> dict:
    """What the Access page needs to decide what to offer. AI Generation
    extras are only offered to an account that already has access (a
    superadmin never needs them)."""

    amount, days, currency = _price(db)
    ready = _gateway_ready(db)
    enabled = ready and amount > 0 and days > 0

    ai_products: list[dict] = []
    if ready and user is not None and user.role != "superadmin":
        grant = current_grant(db, user)
        if grant is not None and grant.expires_at > utcnow():
            for product in products.AI_PRODUCTS:
                price = float(app_settings.get_value(db, product.price_setting) or 0)
                if price > 0:
                    ai_products.append(
                        {"key": product.key, "label": product.label, "amount": price, "currency": currency or "GH₵"}
                    )
    return {"enabled": enabled, "amount": amount, "days": days, "currency": currency, "ai_products": ai_products}


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


def start_checkout(db: Session, user: User, product_key: str = products.ACCESS) -> Payment:
    if product_key == products.ACCESS:
        opts = options(db)
        if not opts["enabled"]:
            raise PaymentError("Online payment isn't available right now -- contact an admin for an access code.")
        amount, days, description_what = opts["amount"], opts["days"], None
    else:
        product = products.BY_KEY.get(product_key)
        offered = {p["key"]: p for p in options(db, user)["ai_products"]}
        if product is None or product_key not in offered:
            raise PaymentError("That isn't available right now.")
        amount, days, description_what = offered[product_key]["amount"], 0, product.label

    config = hubtel.resolve_config(db)
    site = str(app_settings.get_value(db, "public_site_url") or "").rstrip("/")
    if not site:
        raise PaymentError("Online payment isn't fully set up yet -- contact an admin for an access code.")
    site_name = str(app_settings.get_value(db, "site_name") or "Socca Intelligence")

    payment = Payment(
        user_id=user.id,
        product=product_key,
        client_reference=_new_reference(),
        amount=amount,
        access_days=days,
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)

    reference = payment.client_reference
    try:
        result = hubtel.create_checkout(
            config,
            amount=payment.amount,
            description=description_what or f"{payment.access_days}-day access to {site_name}",
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


def _payment_note(payment: Payment) -> str:
    return f"Hubtel payment {payment.client_reference}"


def _fulfil(db: Session, payment: Payment) -> None:
    """Cuts and redeems the access code for a confirmed payment. Exactly one
    caller wins the pending -> fulfilling claim, so two callbacks (Hubtel
    retries) racing each other can't grant twice.

    Safe to repeat for the same payment: the code is looked up by the
    payment's own reference before one is cut, and a code this account has
    already redeemed is not redeemed again -- so a grant interrupted halfway
    is finished on the next attempt rather than doubled."""

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

    if payment.product != products.ACCESS:
        _apply_ai_product(db, payment, user)
        return

    try:
        code = db.execute(
            select(AccessCode).where(AccessCode.notes == _payment_note(payment)).order_by(AccessCode.id)
        ).scalars().first()
        if code is None:
            code = create_access_code(
                db,
                user,
                duration_days=payment.access_days,
                redemption_limit=1,
                assigned_user_id=user.id,
                assigned_email=user.email,
                notes=_payment_note(payment),
                kind="issued",
            )
        already_redeemed = db.execute(
            select(AccessRedemption.id).where(
                AccessRedemption.access_code_id == code.id, AccessRedemption.user_id == user.id
            )
        ).first()
        if already_redeemed is None:
            redeem_access_code(db, user, code.code)
    except Exception:
        # Paid but not granted: back to pending so the next callback, poll or
        # sweep retries. Any failure, not only a refused code -- a database
        # error here must not leave the payment "fulfilling" for good. The
        # admin-issued code is the manual way out if it sticks.
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


def _apply_ai_product(db: Session, payment: Payment, user: User) -> None:
    """Credits or a day pass, applied together with marking the payment
    completed in one commit -- either both happen or neither does."""

    product = products.BY_KEY.get(payment.product)
    try:
        if product is None:
            raise AccessCodeError(f"Unknown product {payment.product!r}")
        now = utcnow()
        if product.kind == "ai_credits":
            db.execute(
                update(User)
                .where(User.id == user.id)
                .values(ai_generation_credits=func.coalesce(User.ai_generation_credits, 0) + product.credits)
            )
        else:
            current = user.ai_unlimited_until
            base = current if current is not None and current > now else now
            user.ai_unlimited_until = base + dt.timedelta(hours=product.hours)
        payment.status = "completed"
        payment.completed_at = now
        _audit(db, user, "payment.completed", payment, {"amount": payment.amount, "product": payment.product})
        db.commit()
    except Exception:
        db.rollback()
        payment = db.get(Payment, payment.id)
        payment.status = "pending"
        db.commit()
        raise


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
        allowed, _ = rate_limit.shared_check(db, "hubtel:status", GLOBAL_STATUS_CHECKS_PER_MINUTE, 60)
        if not allowed:
            return payment
    else:
        rate_limit.shared_check(db, "hubtel:status", GLOBAL_STATUS_CHECKS_PER_MINUTE, 60)

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
        definitive = str(result.raw.get("status") or "").strip().lower() not in hubtel.AMBIGUOUS_UNPAID
        if definitive or now - payment.created_at >= UNPAID_GRACE:
            payment.status = "cancelled"
            _audit(db, None, "payment.cancelled", payment)
            db.commit()

    db.refresh(payment)
    return payment


def sweep_pending(db: Session) -> list[str]:
    """Settles payments whose callback never arrived (a lost callback, or a
    customer who closed the page): asks Hubtel about each unsettled payment
    and acts on the answer exactly as the callback would. Does nothing until
    the gateway is configured. Returns a line per payment that changed, or a
    single line if Hubtel couldn't be reached (the rest are tried next pass)."""

    config = hubtel.resolve_config(db)
    if not config.configured:
        return []
    now = utcnow()
    lines: list[str] = []

    # Payments interrupted mid-grant go back to "pending" first, so the query
    # below picks them up like any other unsettled payment.
    stuck = db.execute(
        update(Payment)
        .where(
            Payment.status == "fulfilling",
            func.coalesce(Payment.last_checked_at, Payment.created_at) <= now - FULFILLING_STUCK_AFTER,
        )
        .values(status="pending", last_checked_at=None)
    )
    db.commit()
    if stuck.rowcount:
        lines.append(f"{stuck.rowcount} payment(s) interrupted while granting; checking again")

    due = (
        db.query(Payment)
        .filter(
            Payment.status == "pending",
            Payment.created_at <= now - SWEEP_MIN_AGE,
            Payment.created_at >= now - SWEEP_MAX_AGE,
            (Payment.last_checked_at.is_(None)) | (Payment.last_checked_at <= now - SWEEP_RECHECK_EVERY),
        )
        .order_by(func.coalesce(Payment.last_checked_at, Payment.created_at))
        .limit(SWEEP_BATCH)
        .all()
    )
    for payment in due:
        before = payment.status
        try:
            reconcile(db, payment, force=True)
        except hubtel.HubtelError:
            lines.append("Hubtel couldn't be reached; will try again")
            break
        except Exception as exc:  # noqa: BLE001 - one bad payment must not stop the rest
            db.rollback()
            lines.append(f"{payment.client_reference}: paid but access couldn't be granted ({exc})")
            continue
        if payment.status != before:
            lines.append(f"{payment.client_reference}: {before} -> {payment.status}")
    return lines
