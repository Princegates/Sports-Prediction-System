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
