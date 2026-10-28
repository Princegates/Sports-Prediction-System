"""Admin direct messages: an email to registered members, from the platform's
own sender. Delivery itself is faked -- what's under test is who can send,
who can be sent to, and what gets logged."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import app_settings, mailer
from app.auth.passwords import hash_password
from app.auth.tokens import create_token
from app.config import get_settings
from app.db.models import AuditLog, User
from app.main import app

client = TestClient(app)


def _user(db, email, role="user"):
    user = User(email=email, name=email.split("@")[0], password_hash=hash_password("a-good-password"), role=role, status="active")
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _headers(user):
    s = get_settings()
    return {"Authorization": f"Bearer {create_token({'user_id': user.id, 'role': user.role}, s.secret_key, s.session_ttl_seconds)}"}


@pytest.fixture()
def admin(db_session):
    return _user(db_session, "admin@example.com", role="superadmin")


@pytest.fixture()
def outbox(monkeypatch):
    """Pretends email is configured and records what would have been sent."""

    sent: list[tuple[str, str, str]] = []
    monkeypatch.setattr(mailer, "is_configured", lambda db=None: True)

    def fake_send(to, subject, body, db=None):
        sent.append((to, subject, body))
        return mailer.SendResult(sent=True)

    monkeypatch.setattr(mailer, "send_email", fake_send)
    return sent


def _send(admin, **overrides):
    body = {"recipients": ["member@example.com"], "subject": "Hello", "message": "A note for you."}
    body.update(overrides)
    return client.post("/api/admin/messages", json=body, headers=_headers(admin))


def test_a_member_cannot_send_messages(db_session, outbox):
    member = _user(db_session, "member@example.com")
    assert client.post("/api/admin/messages", json={"recipients": ["a@b.com"], "subject": "x", "message": "y"}, headers=_headers(member)).status_code == 403
    assert outbox == []


def test_requires_login(outbox):
    assert client.post("/api/admin/messages", json={}).status_code == 401


def test_sends_to_a_registered_member(db_session, admin, outbox):
    _user(db_session, "member@example.com")

    body = _send(admin).json()

    assert body == {"results": [{"email": "member@example.com", "sent": True, "error": None}], "not_registered": []}
    to, subject, text = outbox[0]
    assert (to, subject) == ("member@example.com", "Hello")
    assert text.startswith("A note for you.") and "Socca Intelligence" in text


def test_an_unregistered_address_is_reported_and_never_emailed(db_session, admin, outbox):
    _user(db_session, "member@example.com")

    body = _send(admin, recipients=["member@example.com", "stranger@elsewhere.com"]).json()

    assert body["not_registered"] == ["stranger@elsewhere.com"]
    assert [to for to, _, _ in outbox] == ["member@example.com"]


def test_recipient_matching_ignores_case_and_duplicates(db_session, admin, outbox):
    _user(db_session, "member@example.com")

    body = _send(admin, recipients=["Member@Example.com", " member@example.com "]).json()

    assert len(body["results"]) == 1 and len(outbox) == 1


def test_a_newline_in_the_subject_cannot_smuggle_a_header(db_session, admin, outbox):
    _user(db_session, "member@example.com")

    _send(admin, subject="Hi\r\nBcc: attacker@evil.com")

    assert "\n" not in outbox[0][1] and "\r" not in outbox[0][1]


def test_a_failed_delivery_is_reported_per_recipient(db_session, admin, monkeypatch):
    _user(db_session, "member@example.com")
    monkeypatch.setattr(mailer, "is_configured", lambda db=None: True)
    monkeypatch.setattr(mailer, "send_email", lambda *a, **k: mailer.SendResult(sent=False, error="SMTPException: boom"))

    body = _send(admin).json()

    assert body["results"][0] == {"email": "member@example.com", "sent": False, "error": "SMTPException: boom"}


def test_refused_when_email_is_not_configured(db_session, admin):
    _user(db_session, "member@example.com")
    response = _send(admin)
    assert response.status_code == 400
    assert "Email is not configured" in response.json()["detail"]


def test_blank_subject_or_message_is_rejected(db_session, admin, outbox):
    _user(db_session, "member@example.com")
    assert _send(admin, subject="   ").status_code == 422
    assert _send(admin, message="   ").status_code == 422
    assert _send(admin, recipients=[]).status_code == 422
    assert outbox == []


def test_each_send_is_audit_logged_without_the_body(db_session, admin, outbox):
    member = _user(db_session, "member@example.com")

    _send(admin, message="A private note")

    entry = db_session.query(AuditLog).filter(AuditLog.action == "admin.message_sent").one()
    assert entry.actor_user_id == admin.id and entry.target_user_id == member.id
    assert entry.detail == {"subject": "Hello", "delivered": True}


def test_sending_is_rate_limited(db_session, admin, outbox):
    _user(db_session, "member@example.com")
    codes = [_send(admin).status_code for _ in range(22)]
    assert codes[:20] == [200] * 20 and 429 in codes[20:]
