"""The betting-site reachability check, against canned responses.

Nothing here contacts a real site: requests.get is replaced, so the tests pin
how each kind of answer is read -- a clean page, a redirect to another
country's site, a refusal, a bot challenge, no connection at all.
"""

from __future__ import annotations

import pytest
import requests
from fastapi.testclient import TestClient

from app.auth.passwords import hash_password
from app.auth.tokens import create_token
from app.betcode import reachability
from app.config import get_settings
from app.db.models import User
from app.main import app

client = TestClient(app)


class _FakeResponse:
    def __init__(self, status_code: int, url: str, text: str = "", headers: dict | None = None):
        self.status_code = status_code
        self.url = url
        self.text = text
        self.headers = headers or {}


def _fake_get(response=None, exc=None, seen=None):
    def fake(url, **kwargs):
        if seen is not None:
            seen.append(kwargs)
        if exc is not None:
            raise exc
        return response if not callable(response) else response(url)

    return fake


def test_a_plain_page_is_reachable(monkeypatch):
    monkeypatch.setattr(reachability.requests, "get", _fake_get(_FakeResponse(200, "https://www.sportybet.com/ng/")))

    check = reachability.check_site("SportyBet Nigeria", "https://www.sportybet.com/ng/")

    assert check.reachable and check.status_code == 200 and check.note == "Reachable."


def test_a_redirect_to_another_site_is_flagged(monkeypatch):
    monkeypatch.setattr(reachability.requests, "get", _fake_get(_FakeResponse(200, "https://www.betway.com/restricted")))

    check = reachability.check_site("Betway Ghana", "https://www.betway.com.gh/")

    assert check.reachable
    assert "redirected to https://www.betway.com/restricted" in check.note


@pytest.mark.parametrize("status", [403, 451])
def test_a_refusal_is_not_reachable(monkeypatch, status):
    monkeypatch.setattr(reachability.requests, "get", _fake_get(_FakeResponse(status, "https://1xbet.ng/")))

    check = reachability.check_site("1xBet Nigeria", "https://1xbet.ng/")

    assert not check.reachable
    assert f"HTTP {status}" in check.note


def test_a_bot_challenge_is_named_as_one(monkeypatch):
    challenge = _FakeResponse(503, "https://1xbet.com/", text="<title>Just a moment...</title>")
    monkeypatch.setattr(reachability.requests, "get", _fake_get(challenge))

    check = reachability.check_site("1xBet", "https://1xbet.com/")

    assert not check.reachable
    assert "bot challenge" in check.note


def test_no_connection_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(reachability.requests, "get", _fake_get(exc=requests.ConnectionError("dns failure")))

    check = reachability.check_site("SportyBet Ghana", "https://www.sportybet.com/gh/")

    assert not check.reachable and check.status_code is None
    assert "ConnectionError" in check.note


def test_requests_identify_themselves_honestly(monkeypatch):
    """The check must see what a real connection would, so it never poses
    as a browser."""

    seen: list[dict] = []
    monkeypatch.setattr(reachability.requests, "get", _fake_get(_FakeResponse(200, "https://1xbet.com/"), seen=seen))

    reachability.check_site("1xBet", "https://1xbet.com/")

    assert seen[0]["headers"]["User-Agent"] == reachability.USER_AGENT


def _headers(db, role: str) -> dict:
    user = User(email=f"{role}-sites@example.com", name=role, password_hash=hash_password("a-good-password"),
                role=role, status="active")
    db.add(user)
    db.commit()
    db.refresh(user)
    settings = get_settings()
    token = create_token({"user_id": user.id, "role": user.role}, settings.secret_key, settings.session_ttl_seconds)
    return {"Authorization": f"Bearer {token}"}


def test_the_endpoint_checks_every_site_for_a_superadmin_only(db_session, monkeypatch):
    monkeypatch.setattr(reachability.requests, "get", _fake_get(lambda url: _FakeResponse(200, url)))

    assert client.post("/api/admin/betting-sites/check", headers=_headers(db_session, "user")).status_code == 403

    response = client.post("/api/admin/betting-sites/check", headers=_headers(db_session, "superadmin"))
    assert response.status_code == 200
    body = response.json()
    assert [row["name"] for row in body] == [name for name, _ in reachability.BETTING_SITES]
    assert all(row["reachable"] for row in body)
