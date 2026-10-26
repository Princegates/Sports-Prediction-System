"""A thin client for Hubtel's Online Checkout -- nothing here touches the
database or grants anything; app.payments.service decides what a result means.

Flow: ``create_checkout`` asks Hubtel for a hosted payment page (mobile money
and cards) and returns its URL. Hubtel later POSTs a notification to our
callback URL, but that body is never trusted on its own -- ``check_status``
asks Hubtel directly whether the reference was paid, and only that answer
counts.

Unverified against a live account, so flagged rather than assumed:

- the Status Check API's exact path and response shape (the docs describe it
  but the extracted pages dropped the URL) -- ``check_status`` parses
  defensively and the base URL is a setting;
- whether callbacks are signed -- if Hubtel confirms they are, verify the
  signature in the callback route *in addition to* the status check;
- Hubtel only answers the status API from a whitelisted static IP, and
  rate-limits these endpoints to roughly 5 requests a minute.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import requests
from sqlalchemy.orm import Session

from app.config import get_settings

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 15


class HubtelError(RuntimeError):
    """Hubtel couldn't be reached, refused the request, or sent something
    unintelligible -- the payment's real state is unknown, not "unpaid"."""


@dataclass(frozen=True)
class HubtelConfig:
    client_id: str
    client_secret: str
    merchant_account: str
    initiate_url: str
    status_url: str
    public_api_url: str

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret and self.merchant_account and self.public_api_url)


@dataclass(frozen=True)
class CheckoutResult:
    checkout_id: str | None
    checkout_url: str
    direct_url: str | None = None


@dataclass(frozen=True)
class StatusResult:
    # "paid", "unpaid" (Hubtel says it failed / was cancelled) or "pending".
    state: str
    amount: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def resolve_config(db: Session | None = None) -> HubtelConfig:
    """Database overrides first, environment underneath -- same layering as
    app.mailer.resolve_config."""

    settings = get_settings()
    values: dict[str, Any] = {}
    if db is not None:
        from app import app_settings

        values = app_settings.all_values(db)

    def pick(key: str, fallback: str) -> str:
        return str(values.get(key, fallback) or "") if values else str(fallback or "")

    return HubtelConfig(
        client_id=pick("hubtel_client_id", settings.hubtel_client_id),
        client_secret=pick("hubtel_client_secret", settings.hubtel_client_secret),
        merchant_account=pick("hubtel_merchant_account", settings.hubtel_merchant_account),
        initiate_url=pick("hubtel_initiate_url", settings.hubtel_initiate_url),
        status_url=pick("hubtel_status_url", settings.hubtel_status_url).rstrip("/"),
        public_api_url=pick("public_api_url", settings.public_api_url).rstrip("/"),
    )


def create_checkout(
    config: HubtelConfig,
    *,
    amount: float,
    description: str,
    client_reference: str,
    callback_url: str,
    return_url: str,
    cancellation_url: str,
) -> CheckoutResult:
    body = {
        "totalAmount": amount,
        "description": description,
        "callbackUrl": callback_url,
        "returnUrl": return_url,
        "cancellationUrl": cancellation_url,
        "merchantAccountNumber": config.merchant_account,
        "clientReference": client_reference,
    }
    try:
        response = requests.post(
            config.initiate_url, json=body, auth=(config.client_id, config.client_secret), timeout=TIMEOUT_SECONDS
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Hubtel checkout initiation failed: %s", exc)
        raise HubtelError("Couldn't reach Hubtel to start the payment.") from exc

    data = payload.get("data") if isinstance(payload, dict) else None
    data = data if isinstance(data, dict) else {}
    url = data.get("checkoutUrl")
    if not url:
        logger.warning("Hubtel checkout response had no checkoutUrl: %s", payload)
        raise HubtelError("Hubtel didn't return a payment page.")
    return CheckoutResult(
        checkout_id=str(data["checkoutId"]) if data.get("checkoutId") else None,
        checkout_url=str(url),
        direct_url=str(data["checkoutDirectUrl"]) if data.get("checkoutDirectUrl") else None,
    )


_PAID = {"paid", "success", "successful", "completed"}
_FAILED = {"unpaid", "failed", "cancelled", "canceled", "declined", "expired"}


def check_status(config: HubtelConfig, client_reference: str) -> StatusResult:
    """Ask Hubtel directly whether ``client_reference`` was paid. Raises
    HubtelError when the answer can't be obtained -- the caller must treat
    that as "don't know", never as "not paid"."""

    url = f"{config.status_url}/transactions/{config.merchant_account}/status"
    try:
        response = requests.get(
            url,
            params={"clientReference": client_reference},
            auth=(config.client_id, config.client_secret),
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Hubtel status check failed for %s: %s", client_reference, exc)
        raise HubtelError("Couldn't confirm the payment with Hubtel.") from exc

    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise HubtelError("Hubtel's status response wasn't understood.")

    status = str(data.get("status") or "").strip().lower()
    amount_raw = data.get("amount")
    try:
        amount = float(amount_raw) if amount_raw is not None else None
    except (TypeError, ValueError):
        amount = None

    if status in _PAID:
        state = "paid"
    elif status in _FAILED:
        state = "unpaid"
    else:
        state = "pending"
    return StatusResult(state=state, amount=amount, raw=data)


def callback_reference(payload: Any) -> str | None:
    """The client reference named in a callback body -- used only to find the
    payment to verify, never as proof that it was paid."""

    if not isinstance(payload, dict):
        return None
    data = payload.get("Data") or payload.get("data")
    if not isinstance(data, dict):
        return None
    reference = data.get("ClientReference") or data.get("clientReference")
    return str(reference) if reference else None
