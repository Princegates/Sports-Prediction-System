"""Issuing a code: one registered account, one redemption, optional email.

The product decision these encode is that a code belongs to a person. You
register, find you have no access, pay, and are issued a code of your own --
so ``redemption_limit`` is fixed at 1 rather than offered as a choice, and
the account must exist before a code can be cut for it. A shareable code
here is not a feature, it is a way to give access away by accident.

Email is optional everywhere. The tests that matter most are the failure
ones: a deployment with no SMTP configured, and a send that fails outright,
must both still hand back a usable code. Losing a code to a mail error would
be worse than never sending it, because the admin ends up with neither.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import mailer
from app.auth.passwords import hash_password
from app.auth.tokens import create_token
from app.config import get_settings
from app.db.models import AccessCode, User
from app.main import app

client = TestClient(app)


def _user(db, email: str, role: str = "user") -> User:
    user = User(email=email, name=email.split("@")[0], password_hash=hash_password("a-good-password"),
                role=role, status="active")
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _headers(user: User) -> dict:
    settings = get_settings()
    token = create_token({"user_id": user.id, "role": user.role}, settings.secret_key, settings.session_ttl_seconds)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def admin(db_session):
    return _user(db_session, "boss@example.com", role="superadmin")


# --- the code belongs to one account -----------------------------------


def test_code_is_bound_to_the_registered_account_and_single_use(db_session, admin):
    member = _user(db_session, "buyer@example.com")

    response = client.post(
        "/api/admin/access-codes",
        json={"duration_days": 7, "assigned_user_email": "buyer@example.com", "notes": "MOMO-123"},
        headers=_headers(admin),
    )
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["assigned_user_id"] == member.id
    assert body["assigned_email"] == "buyer@example.com"
    # Not configurable, and not something the caller can raise.
    assert body["redemption_limit"] == 1


def test_a_redemption_limit_cannot_be_smuggled_in(db_session, admin):
    """The field is gone from the schema, so passing it must not take effect
    -- otherwise 'removed from the form' would be cosmetic."""

    _user(db_session, "buyer@example.com")
    response = client.post(
        "/api/admin/access-codes",
        json={"duration_days": 7, "assigned_user_email": "buyer@example.com", "redemption_limit": 50},
        headers=_headers(admin),
    )
    assert response.status_code == 200, response.text
    assert response.json()["redemption_limit"] == 1


def test_email_is_matched_case_insensitively(db_session, admin):
    member = _user(db_session, "buyer@example.com")
    response = client.post(
        "/api/admin/access-codes",
        json={"duration_days": 3, "assigned_user_email": "  BUYER@Example.COM  "},
        headers=_headers(admin),
    )
    assert response.status_code == 200, response.text
    assert response.json()["assigned_user_id"] == member.id


def test_unregistered_email_is_refused_with_an_actionable_message(db_session, admin):
    """A typo must fail now, not produce a code nobody can ever redeem."""

    response = client.post(
        "/api/admin/access-codes",
        json={"duration_days": 7, "assigned_user_email": "typo@example.com"},
        headers=_headers(admin),
    )
    assert response.status_code == 404
    detail = response.json()["detail"]
    assert "typo@example.com" in detail
    assert "register" in detail.lower()


def test_email_is_required(db_session, admin):
    response = client.post(
        "/api/admin/access-codes", json={"duration_days": 7}, headers=_headers(admin)
    )
    assert response.status_code == 422


def test_only_the_assigned_account_can_redeem(db_session, admin):
    _user(db_session, "buyer@example.com")
    stranger = _user(db_session, "someone.else@example.com")

    created = client.post(
        "/api/admin/access-codes",
        json={"duration_days": 7, "assigned_user_email": "buyer@example.com"},
        headers=_headers(admin),
    ).json()

    response = client.post(
        "/api/access/redeem", json={"code": created["code"]}, headers=_headers(stranger)
    )
    assert response.status_code == 400
    assert "different account" in response.json()["detail"].lower()


# --- email delivery is optional and never costs you the code -----------


def test_code_survives_an_unconfigured_mail_server(db_session, admin, monkeypatch):
    _user(db_session, "buyer@example.com")
    monkeypatch.setattr(mailer, "is_configured", lambda *a, **k: False)

    response = client.post(
        "/api/admin/access-codes",
        json={"duration_days": 7, "assigned_user_email": "buyer@example.com", "send_email": True},
        headers=_headers(admin),
    )
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["emailed"] is False
    assert "not configured" in body["email_error"].lower()
    # The point of the test: the code is still there and still real.
    assert body["code"]
    assert db_session.query(AccessCode).filter_by(code=body["code"]).one()


def test_code_survives_a_failed_send(db_session, admin, monkeypatch):
    _user(db_session, "buyer@example.com")
    monkeypatch.setattr(mailer, "is_configured", lambda *a, **k: True)
    monkeypatch.setattr(
        mailer, "send_email", lambda *a, **k: mailer.SendResult(sent=False, error="SMTPAuthenticationError: bad password")
    )

    response = client.post(
        "/api/admin/access-codes",
        json={"duration_days": 7, "assigned_user_email": "buyer@example.com", "send_email": True},
        headers=_headers(admin),
    )
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["emailed"] is False
    assert "bad password" in body["email_error"]
    assert db_session.query(AccessCode).filter_by(code=body["code"]).one()


def test_successful_send_is_reported_and_goes_to_the_assigned_address(db_session, admin, monkeypatch):
    _user(db_session, "buyer@example.com")
    sent: list[tuple] = []
    monkeypatch.setattr(mailer, "is_configured", lambda *a, **k: True)
    monkeypatch.setattr(
        mailer, "send_email",
        lambda to, subject, body, **kwargs: (sent.append((to, subject, body)), mailer.SendResult(sent=True))[1],
    )

    response = client.post(
        "/api/admin/access-codes",
        json={"duration_days": 7, "assigned_user_email": "buyer@example.com", "send_email": True},
        headers=_headers(admin),
    )
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["emailed"] is True
    assert body["email_error"] is None

    assert len(sent) == 1
    to, subject, text = sent[0]
    assert to == "buyer@example.com"
    assert body["code"] in text
    assert "7 days" in text


def test_nothing_is_sent_unless_asked(db_session, admin, monkeypatch):
    _user(db_session, "buyer@example.com")
    monkeypatch.setattr(mailer, "is_configured", lambda *a, **k: True)
    monkeypatch.setattr(mailer, "send_email", lambda *a, **k: pytest.fail("sent without send_email"))

    response = client.post(
        "/api/admin/access-codes",
        json={"duration_days": 7, "assigned_user_email": "buyer@example.com"},
        headers=_headers(admin),
    )
    assert response.status_code == 200, response.text
    assert response.json()["emailed"] is False


# --- resending a code that already exists -------------------------------


def _issue_code(admin, email: str, **overrides) -> dict:
    payload = {"duration_days": 7, "assigned_user_email": email, "send_email": False}
    payload.update(overrides)
    response = client.post("/api/admin/access-codes", json=payload, headers=_headers(admin))
    assert response.status_code == 200, response.text
    return response.json()


def test_resend_delivers_the_same_code_to_the_assigned_address(db_session, admin, monkeypatch):
    _user(db_session, "buyer@example.com")
    created = _issue_code(admin, "buyer@example.com")

    sent: list[tuple] = []
    monkeypatch.setattr(mailer, "is_configured", lambda *a, **k: True)
    monkeypatch.setattr(
        mailer, "send_email",
        lambda to, subject, body, **kwargs: (sent.append((to, subject, body)), mailer.SendResult(sent=True))[1],
    )

    response = client.post(f"/api/admin/access-codes/{created['id']}/resend", headers=_headers(admin))
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["emailed"] is True
    assert body["code"] == created["code"]  # same code, not a new one
    assert db_session.query(AccessCode).filter_by(code=created["code"]).count() == 1

    assert len(sent) == 1
    to, subject, text = sent[0]
    assert to == "buyer@example.com"
    assert created["code"] in text


def test_resend_reports_when_mail_is_not_configured(db_session, admin, monkeypatch):
    _user(db_session, "buyer@example.com")
    created = _issue_code(admin, "buyer@example.com")
    monkeypatch.setattr(mailer, "is_configured", lambda *a, **k: False)

    response = client.post(f"/api/admin/access-codes/{created['id']}/resend", headers=_headers(admin))
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["emailed"] is False
    assert "not configured" in body["email_error"].lower()


def test_resend_reports_a_failed_send(db_session, admin, monkeypatch):
    _user(db_session, "buyer@example.com")
    created = _issue_code(admin, "buyer@example.com")
    monkeypatch.setattr(mailer, "is_configured", lambda *a, **k: True)
    monkeypatch.setattr(
        mailer, "send_email", lambda *a, **k: mailer.SendResult(sent=False, error="SMTPAuthenticationError: bad password")
    )

    response = client.post(f"/api/admin/access-codes/{created['id']}/resend", headers=_headers(admin))
    assert response.status_code == 200, response.text
    assert "bad password" in response.json()["email_error"]


def test_resend_refuses_a_revoked_code(db_session, admin):
    _user(db_session, "buyer@example.com")
    created = _issue_code(admin, "buyer@example.com")

    revoke = client.post(f"/api/admin/access-codes/{created['id']}/revoke", json={}, headers=_headers(admin))
    assert revoke.status_code == 200, revoke.text

    response = client.post(f"/api/admin/access-codes/{created['id']}/resend", headers=_headers(admin))
    assert response.status_code == 400
    assert "revoked" in response.json()["detail"].lower()


def test_resend_returns_404_for_an_unknown_code(db_session, admin):
    response = client.post("/api/admin/access-codes/999999/resend", headers=_headers(admin))
    assert response.status_code == 404
