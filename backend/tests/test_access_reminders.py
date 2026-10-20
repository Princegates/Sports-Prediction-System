"""Automatic email a day before access ends, and the "I've paid" notice."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app.timeutil import utcnow
from app import mailer
from app.access import create_access_code, redeem_access_code
from app.access_reminders import send_expiry_reminders
from app.auth.passwords import hash_password
from app.auth.tokens import create_token
from app.config import get_settings
from app.db.models import AccessGrant, AuditLog, User
from app.main import app

client = TestClient(app)


@pytest.fixture()
def outbox(monkeypatch):
    sent: list[tuple[str, str, str]] = []
    monkeypatch.setattr(mailer, "is_configured", lambda db=None: True)
    monkeypatch.setattr(
        mailer, "send_email", lambda to, subject, body, db=None: sent.append((to, subject, body)) or mailer.SendResult(sent=True)
    )
    return sent


def _user(db, email: str, role: str = "user") -> User:
    user = User(email=email, name="x", password_hash=hash_password("a-good-password"), role=role, status="active")
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _grant(db, user: User, *, kind: str, started_hours_ago: float, ends_in_hours: float) -> AccessGrant:
    code = create_access_code(db, user, duration_days=1, assigned_user_id=user.id, kind=kind)
    grant = redeem_access_code(db, user, code.code)
    now = utcnow()
    grant.activated_at = now - dt.timedelta(hours=started_hours_ago)
    grant.expires_at = now + dt.timedelta(hours=ends_in_hours)
    db.commit()
    return grant


def test_reminds_once_a_day_before_a_trial_ends(db_session, outbox):
    user = _user(db_session, "trialist@example.com")
    _grant(db_session, user, kind="trial", started_hours_ago=96, ends_in_hours=24)

    assert send_expiry_reminders(db_session) == 1
    assert outbox[0][0] == "trialist@example.com"
    assert outbox[0][1] == "Your free trial ends tomorrow"

    assert send_expiry_reminders(db_session) == 0  # never twice for the same grant
    assert len(outbox) == 1


def test_paid_access_gets_the_renewal_wording(db_session, outbox):
    user = _user(db_session, "payer@example.com")
    _grant(db_session, user, kind="issued", started_hours_ago=700, ends_in_hours=22)
    send_expiry_reminders(db_session)
    assert outbox[0][1] == "Your access ends tomorrow"


@pytest.mark.parametrize("started, ends", [(96, 40), (96, 10), (2, 24)])
def test_outside_the_window_or_just_started_is_left_alone(db_session, outbox, started, ends):
    user = _user(db_session, "later@example.com")
    _grant(db_session, user, kind="trial", started_hours_ago=started, ends_in_hours=ends)
    assert send_expiry_reminders(db_session) == 0


def test_no_mail_configured_is_a_no_op(db_session):
    user = _user(db_session, "quiet@example.com")
    _grant(db_session, user, kind="trial", started_hours_ago=96, ends_in_hours=24)
    assert send_expiry_reminders(db_session) == 0


def _headers(user: User) -> dict:
    settings = get_settings()
    return {"Authorization": f"Bearer {create_token({'user_id': user.id}, settings.secret_key, 3600)}"}


def test_payment_notice_alerts_every_superadmin_and_is_logged(db_session, outbox):
    _user(db_session, "boss1@example.com", role="superadmin")
    _user(db_session, "boss2@example.com", role="superadmin")
    member = _user(db_session, "member@example.com")

    response = client.post("/api/access/payment-notice", json={"reference": "MOMO-12345"}, headers=_headers(member))
    assert response.status_code == 200, response.text

    assert sorted(to for (to, _, _) in outbox) == ["boss1@example.com", "boss2@example.com"]
    assert "MOMO-12345" in outbox[0][2]
    entry = db_session.query(AuditLog).filter(AuditLog.action == "access.payment_reported").one()
    assert entry.detail == {"reference": "MOMO-12345"}


def test_payment_notice_is_rate_limited(db_session, outbox):
    member = _user(db_session, "eager@example.com")
    codes = [
        client.post("/api/access/payment-notice", json={"reference": f"ref-{i}"}, headers=_headers(member)).status_code
        for i in range(4)
    ]
    assert codes == [200, 200, 200, 429]
