"""The blanket per-IP throttle in app.main (_global_rate_limit), separate
from the credential-specific limits in app/api/rate_limit.py's own callers.
Before this, nothing stopped a single client from hammering any endpoint at
all, not just login/register/chat.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app

client = TestClient(app)


def _hit(n: int):
    # A cheap, unauthenticated GET any client can reach -- same shape of
    # endpoint a real abusive client would actually hammer.
    return client.get("/api/public/stats", headers={"X-Forwarded-For": "203.0.113.9"})


def test_a_client_over_the_global_limit_gets_429_with_retry_after():
    limit = get_settings().global_rate_limit_requests

    responses = [_hit(i) for i in range(limit + 5)]
    codes = [r.status_code for r in responses]

    assert codes[:limit].count(429) == 0, "the limit itself should not reject legitimate traffic"
    assert 429 in codes[limit:]
    blocked = next(r for r in responses if r.status_code == 429)
    assert "retry-after" in {k.lower() for k in blocked.headers.keys()}


def test_health_endpoint_is_exempt_from_the_global_limit():
    limit = get_settings().global_rate_limit_requests

    codes = [
        client.get("/api/health", headers={"X-Forwarded-For": "203.0.113.10"}).status_code
        for _ in range(limit + 10)
    ]

    assert 429 not in codes


def test_a_throttled_response_still_carries_cors_headers():
    """The rate-limit middleware must sit inside CORSMiddleware in the stack
    -- a 429 missing CORS headers shows up in a browser as a CORS failure,
    hiding the real "too many requests" message from the user."""

    limit = get_settings().global_rate_limit_requests
    responses = [
        client.get(
            "/api/public/stats",
            headers={"X-Forwarded-For": "203.0.113.11", "Origin": "https://soccaintel.com"},
        )
        for _ in range(limit + 3)
    ]
    blocked = next(r for r in responses if r.status_code == 429)
    assert "access-control-allow-origin" in {k.lower() for k in blocked.headers.keys()}
