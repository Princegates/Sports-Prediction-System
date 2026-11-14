"""Admin alert emails: stale predictions and paid-but-ungranted payments."""

from __future__ import annotations

import datetime as dt

import pytest

from app import admin_alerts, mailer
from app.auth.passwords import hash_password
from app.db.models import Payment, User
from app.timeutil import utcnow


@pytest.fixture()
def outbox(monkeypatch):
    sent = []
    monkeypatch.setattr(mailer, "is_configured", lambda db=None: True)
    monkeypatch.setattr(mailer, "send_email", lambda to, s, b, db=None: sent.append((to, s, b)))
    return sent


def _admin(db):
    db.add(User(email="boss@example.com", name="B", password_hash=hash_password("x-password"), role="superadmin", status="active"))
    db.commit()


def _payment(db, ref, *, minutes_ago, status="pending", detail=None):
    db.add(Payment(
        client_reference=ref, amount=20.0, access_days=5, status=status,
        created_at=utcnow() - dt.timedelta(minutes=minutes_ago), provider_detail=detail,
    ))
    db.commit()


def test_a_paid_payment_left_ungranted_is_reported_once(db_session, outbox):
    _admin(db_session)
    _payment(db_session, "PAID-STUCK", minutes_ago=20, detail={"status": "Paid", "amount": 20})
    _payment(db_session, "ABANDONED", minutes_ago=20, detail={"status": "Pending"})
    _payment(db_session, "JUST-PAID", minutes_ago=3, detail={"status": "Paid"})

    assert admin_alerts.send_alerts(db_session) == ["stuck_payments"]
    assert len(outbox) == 1
    body = outbox[0][2]
    assert "PAID-STUCK" in body and "ABANDONED" not in body and "JUST-PAID" not in body

    # Not again within the quiet period, even from another worker.
    assert admin_alerts.send_alerts(db_session) == []
    later = utcnow() + admin_alerts.REPEAT_EVERY + dt.timedelta(minutes=1)
    assert admin_alerts.send_alerts(db_session, now=later) == ["stuck_payments"]


def test_nothing_is_sent_when_all_is_well(db_session, outbox):
    _admin(db_session)
    _payment(db_session, "DONE", minutes_ago=60, status="completed", detail={"status": "Paid"})
    assert admin_alerts.send_alerts(db_session) == []
    assert outbox == []
