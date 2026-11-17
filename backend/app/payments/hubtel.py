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
import re
from dataclasses import dataclass, field
from typing import Any

import requests
from sqlalchemy.orm import Session

from app.config import get_settings

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 15


class HubtelError(RuntimeError):
    """Hubtel couldn't be reached, refused the request, or sent something
    unintelligible -- the payment's real state is unknown, not "unpaid".

    ``detail`` says what actually happened (HTTP status, Hubtel's own reply, a
    plain explanation) for the admin Payments page. It never holds our
    credentials: they are only ever sent, never echoed back."""

    def __init__(self, message: str, detail: dict | None = None):
        super().__init__(message)
        self.detail = detail or {}


# What an HTTP status from the checkout endpoint usually means, in plain words.
_HTTP_MEANING = {
    401: "Hubtel didn't accept our API ID and secret. Copy them again from Hubtel's dashboard "
         "(API & Integrations -> API Credentials) into Settings -> Online payments, and save both together.",
    403: "Hubtel refused the request. The API keys may not be allowed to use Online Checkout yet, or the "
         "account isn't activated for it -- ask your Hubtel engineer.",
    404: "The checkout address is wrong. Set 'Hubtel checkout URL' to "
         "https://payproxyapi.hubtel.com/items/initiate.",
    405: "The checkout address is wrong. Set 'Hubtel checkout URL' to "
         "https://payproxyapi.hubtel.com/items/initiate.",
}
# Hubtel's own response codes, from its Online Checkout document.
_CODE_MEANING = {
    "4000": "Hubtel says something in the request isn't valid -- most often the merchant account number "
            "(it must be your Collection Account Number).",
    "4070": "Hubtel says fees aren't set up for your account, or the amount is below its minimum. Ask your "
            "Hubtel relationship manager to set up fees.",
    "2001": "Hubtel's payment processor reported an error. Try again in a few minutes.",
    "0005": "Hubtel couldn't reach its payment partner, so the state is unknown. Ask your Hubtel engineer.",
}


def _reply(response: requests.Response) -> Any:
    """Hubtel's reply as data where possible, else its text, kept short."""

    try:
        return response.json()
    except ValueError:
        return (response.text or "")[:1000]


def _response_code(reply: Any) -> str | None:
    if isinstance(reply, dict):
        code = _field(reply, "responseCode")
        return str(code) if code is not None else None
    return None


def _explain(status: int, reply: Any) -> str:
    code = _response_code(reply)
    if code in _CODE_MEANING:
        return _CODE_MEANING[code]
    if status in _HTTP_MEANING:
        return _HTTP_MEANING[status]
    if status >= 500:
        return "Hubtel had a problem on its side. Try again in a few minutes."
    if 400 <= status < 500:
        return "Hubtel rejected the request -- see its reply for the reason."
    return "Hubtel answered without a payment page -- see its reply."


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


def _plain_description(text: str) -> str:
    """Hubtel rejects special characters in ``description`` (its own list of
    failure causes names & * ! % @), so keep letters, digits and spaces."""

    cleaned = " ".join(re.sub(r"[^A-Za-z0-9 ]+", " ", text).split())
    return cleaned or "Payment"


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
        "totalAmount": round(float(amount), 2),  # Hubtel allows two decimal places only
        "description": _plain_description(description),
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
    except requests.RequestException as exc:
        logger.warning("Hubtel checkout initiation failed: %s", exc)
        raise HubtelError(
            "Couldn't reach Hubtel to start the payment.",
            {
                "problem": "no connection",
                "explanation": "Our server couldn't connect to Hubtel at all (network, address or timeout). "
                               "Check 'Hubtel checkout URL' and try again; if it persists, the server's "
                               "network may be blocking Hubtel.",
                "technical": type(exc).__name__,
            },
        ) from exc

    reply = _reply(response)
    if response.status_code >= 400:
        detail = {"http_status": response.status_code, "explanation": _explain(response.status_code, reply),
                  "hubtel_reply": reply}
        logger.warning("Hubtel refused checkout initiation: %s", detail)
        raise HubtelError(f"Hubtel refused the payment request (HTTP {response.status_code}).", detail)

    payload = reply if isinstance(reply, dict) else None
    data = _field(payload, "data") if payload is not None else None
    data = data if isinstance(data, dict) else {}
    url = _field(data, "checkoutUrl")
    if not url:
        detail = {"http_status": response.status_code, "explanation": _explain(response.status_code, reply),
                  "hubtel_reply": reply}
        logger.warning("Hubtel checkout response had no checkoutUrl: %s", detail)
        raise HubtelError("Hubtel didn't return a payment page.", detail)
    checkout_id = _field(data, "checkoutId")
    direct_url = _field(data, "checkoutDirectUrl")
    return CheckoutResult(
        checkout_id=str(checkout_id) if checkout_id else None,
        checkout_url=str(url),
        direct_url=str(direct_url) if direct_url else None,
    )


_PAID = {"paid", "success", "successful", "completed"}
_FAILED = {"unpaid", "failed", "cancelled", "canceled", "declined", "expired", "refunded"}
# "Unpaid" is also what Hubtel says while a customer is still approving the
# prompt, so on its own it doesn't prove the payment is dead.
AMBIGUOUS_UNPAID = {"unpaid"}


def _field(obj: dict, *names: str) -> Any:
    """A value from ``obj`` by any of ``names``, ignoring letter case -- Hubtel's
    own documents and responses mix ``data``/``Data`` and ``status``/``Status``."""

    lowered = {str(k).lower(): v for k, v in obj.items()}
    for name in names:
        if name.lower() in lowered:
            return lowered[name.lower()]
    return None


def _transaction_for(payload: Any, client_reference: str) -> dict | None:
    """The one transaction in a status response that belongs to
    ``client_reference``. The response's ``data`` has been seen as a single
    object and as a list of transactions; either way only an entry that names
    our reference (or a lone object that names none) is believed, so another
    customer's payment can never be mistaken for ours. Raises HubtelError when
    the response has no usable shape at all."""

    data = _field(payload, "data") if isinstance(payload, dict) else None
    if isinstance(data, dict):
        named = _field(data, "clientReference")
        return data if named in (None, "") or str(named) == client_reference else None
    if isinstance(data, list):
        for entry in data:
            if isinstance(entry, dict) and str(_field(entry, "clientReference") or "") == client_reference:
                return entry
        return None
    raise HubtelError("Hubtel's status response wasn't understood.")


def says_paid(raw: Any) -> bool:
    """Whether a stored status-check answer (``Payment.provider_detail``)
    reported the payment as paid."""

    if not isinstance(raw, dict):
        return False
    return str(_field(raw, "status", "transactionStatus") or "").strip().lower() in _PAID


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

    data = _transaction_for(payload, client_reference)
    if data is None:
        # Hubtel answered but has no transaction for this reference (yet): not
        # paid as far as anyone can tell, and not a reason to cancel either.
        return StatusResult(state="pending", amount=None, raw={"note": "Hubtel returned no matching transaction"})

    status = str(_field(data, "status", "transactionStatus") or "").strip().lower()
    amount_raw = _field(data, "amount")
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


# --- checking the saved keys (Settings -> Online payments -> Check Hubtel keys) --------


def key_hint(value: str) -> str:
    """Enough of a key to compare with Hubtel's dashboard, never the key: its
    first and last four characters and its length."""

    value = value or ""
    if not value:
        return "not set"
    if len(value) <= 10:
        return f"{len(value)} characters"
    return f"starts {value[:4]}, ends {value[-4:]}, {len(value)} characters"


def secret_hint(value: str) -> str:
    """Less than key_hint, because this one is a secret: only its last four
    characters and its length, the way a card number is shown."""

    value = value or ""
    if not value:
        return "not set"
    if len(value) <= 10:
        return f"{len(value)} characters"
    return f"ends {value[-4:]}, {len(value)} characters"


@dataclass(frozen=True)
class KeyCheck:
    ok: bool
    detail: str
    http_status: int | None


def check_keys(config: HubtelConfig) -> KeyCheck:
    """Whether Hubtel accepts the saved API ID and secret, without creating
    anything: asks the Transaction Status Check about a reference that doesn't
    exist. 401 means the keys are rejected; 403 or no answer means this
    server's IP isn't whitelisted for status checks (which says nothing about
    the keys); any other answer means the keys got through."""

    if not (config.client_id and config.client_secret and config.merchant_account):
        return KeyCheck(False, "The API ID, secret and merchant account number all need to be saved first.", None)
    url = f"{config.status_url}/transactions/{config.merchant_account}/status"
    try:
        response = requests.get(
            url, params={"clientReference": "SI-KEYCHECK"}, auth=(config.client_id, config.client_secret),
            timeout=TIMEOUT_SECONDS,
        )
    except requests.Timeout:
        return KeyCheck(False, "Hubtel didn't answer. For status checks that usually means this server's IP "
                               "isn't whitelisted yet, so this can't tell whether the keys are right.", None)
    except requests.RequestException as exc:
        return KeyCheck(False, f"Couldn't reach Hubtel's status service ({type(exc).__name__}).", None)

    code = response.status_code
    if code == 401:
        return KeyCheck(False, "Rejected (HTTP 401): Hubtel doesn't accept this API ID and secret together. "
                               "Compare the hints below with Hubtel's dashboard.", code)
    if code == 403:
        return KeyCheck(False, "Hubtel refused (HTTP 403) because this server's IP isn't whitelisted for status "
                               "checks, so this can't tell whether the keys are right. Ask Hubtel to whitelist it.", code)
    if code >= 500:
        return KeyCheck(False, f"Hubtel had a problem on its side (HTTP {code}). Try again shortly.", code)
    return KeyCheck(True, f"Accepted: Hubtel took these keys (HTTP {code} for a made-up reference, as expected).", code)
