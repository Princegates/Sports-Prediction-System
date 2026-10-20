"""Session tokens -- a minimal HMAC-signed bearer token, stdlib only. Not a
full JWT implementation (no algorithm negotiation, no third-party claims) --
deliberately so, since this is one backend issuing tokens to one frontend,
not a multi-service setup that would need that flexibility.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

DEFAULT_TTL_SECONDS = 7 * 24 * 60 * 60  # 7 days


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def create_token(payload: dict[str, Any], secret: str, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> str:
    body = {**payload, "exp": time.time() + ttl_seconds}
    body_b64 = _b64encode(json.dumps(body, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(secret.encode("utf-8"), body_b64.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body_b64}.{signature}"


def verify_token(token: str, secret: str) -> dict[str, Any] | None:
    try:
        body_b64, signature = token.split(".", 1)
    except ValueError:
        return None

    expected = hmac.new(secret.encode("utf-8"), body_b64.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None

    try:
        payload = json.loads(_b64decode(body_b64))
    except (ValueError, json.JSONDecodeError):
        return None

    if payload.get("exp", 0) < time.time():
        return None
    return payload


# Single-purpose tokens (email confirmation, password reset) share the
# signing key with session tokens, so each carries a "purpose" claim and
# get_current_user refuses any token that has one -- an emailed link can
# never be replayed as a login, and a session token can never confirm an
# email or reset a password.
VERIFY_EMAIL = "verify_email"
RESET_PASSWORD = "reset_password"


def create_purpose_token(purpose: str, payload: dict[str, Any], secret: str, ttl_seconds: int) -> str:
    return create_token({**payload, "purpose": purpose}, secret, ttl_seconds)


def verify_purpose_token(token: str, purpose: str, secret: str) -> dict[str, Any] | None:
    payload = verify_token(token, secret)
    if payload is None or payload.get("purpose") != purpose:
        return None
    return payload


def password_fingerprint(password_hash: str) -> str:
    """A short digest of the stored hash, embedded in a reset token so the
    token dies the moment the password changes -- which is what makes a
    stateless reset link single-use."""

    return hashlib.sha256(password_hash.encode("utf-8")).hexdigest()[:16]
