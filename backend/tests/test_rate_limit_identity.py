"""Rate limits that a forged X-Forwarded-For header can't get around.

Every limit used to be keyed on the left-most X-Forwarded-For entry, which
the client writes -- sending a different made-up address per request beat
all of them. The limits that matter are now keyed on the account being
attacked or the authenticated user; these tests rotate the header on every
request to prove that no longer helps.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from starlette.requests import Request

from app.api import rate_limit
from app.auth.passwords import hash_password
from app.config import get_settings
from app.db.models import User
from app.main import app

client = TestClient(app)


def _make_user(db_session, email: str = "target@example.com") -> User:
    user = User(email=email, name="Target", password_hash=hash_password("the-real-password"), role="user", status="active")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _login(email: str, password: str, n: int):
    return client.post(
        "/api/auth/login",
        json={"email": email, "password": password},
        headers={"X-Forwarded-For": f"10.{n // 250}.{n % 250}.1"},
    )


def test_rotating_forwarded_for_does_not_escape_the_per_account_login_limit(db_session):
    _make_user(db_session)
    limit = get_settings().login_account_failure_limit

    codes = [_login("target@example.com", f"guess-{i}", i).status_code for i in range(limit + 5)]

    assert codes[:limit] == [401] * limit
    assert set(codes[limit:]) == {429}


def test_the_account_limit_also_blocks_the_right_password_until_it_drains(db_session):
    """Otherwise the limit would just tell an attacker which guess was right."""

    _make_user(db_session)
    limit = get_settings().login_account_failure_limit
    for i in range(limit):
        _login("target@example.com", f"guess-{i}", i)

    assert _login("target@example.com", "the-real-password", 999).status_code == 429


def test_successful_logins_do_not_count_against_the_account(db_session):
    _make_user(db_session)
    limit = get_settings().login_account_failure_limit
    for i in range(limit + 3):
        assert _login("target@example.com", "the-real-password", i).status_code == 200


def test_unknown_emails_are_limited_the_same_way(db_session):
    limit = get_settings().login_account_failure_limit
    codes = [_login("nobody@example.com", f"guess-{i}", i).status_code for i in range(limit + 1)]
    assert codes[-1] == 429


def test_account_status_shares_the_login_failure_counter(db_session):
    _make_user(db_session)
    limit = get_settings().login_account_failure_limit
    for i in range(limit):
        _login("target@example.com", f"guess-{i}", i)

    response = client.post(
        "/api/auth/status",
        json={"email": "target@example.com", "password": "another-guess"},
        headers={"X-Forwarded-For": "203.0.113.77"},
    )
    assert response.status_code == 429


def test_total_signups_are_capped_even_when_every_request_claims_a_new_ip(db_session, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "register_global_limit", 3)

    codes = []
    for i in range(5):
        response = client.post(
            "/api/auth/register",
            json={"email": f"sock{i}@example.com", "name": "x", "password": "password123", "date_of_birth": "1990-01-01"},
            headers={"X-Forwarded-For": f"198.51.100.{i}"},
        )
        codes.append(response.status_code)

    assert codes[:3] == [200, 200, 200]
    assert codes[3:] == [429, 429]


def _request(headers: dict[str, str], peer: str = "192.0.2.10") -> Request:
    scope = {
        "type": "http",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": (peer, 1234),
    }
    return Request(scope)


def test_client_ip_prefers_the_edge_header_over_the_forwarded_chain():
    request = _request({"CF-Connecting-IP": "203.0.113.5", "X-Forwarded-For": "6.6.6.6, 203.0.113.5"})
    assert rate_limit.client_ip(request) == "203.0.113.5"


def test_client_ip_falls_back_to_forwarded_then_peer():
    assert rate_limit.client_ip(_request({"X-Forwarded-For": "203.0.113.9, 10.0.0.1"})) == "203.0.113.9"
    assert rate_limit.client_ip(_request({})) == "192.0.2.10"
