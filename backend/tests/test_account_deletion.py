"""Admin -> Users -> Delete: the account and everything that's the person's
own goes; platform records stay but stop pointing at them."""

from __future__ import annotations

import datetime as dt

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import AccessCode, AccessGrant, AuditLog, BookingSlip, ChatMessage, User
from app.main import app
from app.timeutil import utcnow
from tests.conftest import grant_active_access
from tests.test_admin_hardening import _headers, _make_user

client = TestClient(app)


def test_deleting_a_user_removes_them_and_their_own_data(db_session):
    admin = _make_user(db_session, "admin@example.com", role="superadmin")
    target = _make_user(db_session, "gone@example.com")
    referred = _make_user(db_session, "friend@example.com")
    referred.referred_by_user_id = target.id
    grant_active_access(db_session, target)  # its code names the target as creator
    assigned = AccessCode(code="ASSIGNED-1", duration_days=5, created_by_user_id=admin.id,
                          assigned_user_id=target.id, assigned_email=target.email)
    db_session.add_all([
        assigned,
        ChatMessage(user_id=target.id, role="user", content="hello"),
        BookingSlip(user_id=target.id, bookmaker="sportybet", criteria={}, legs=[], combined_probability=0.5,
                    expires_at=utcnow() + dt.timedelta(days=1), provider="x"),
        AuditLog(actor_user_id=target.id, actor_email=target.email, action="access.payment_reported", target_user_id=target.id),
    ])
    db_session.commit()
    target_id = target.id

    response = client.delete(f"/api/admin/users/{target_id}", headers=_headers(admin))
    assert response.status_code == 200
    db_session.expire_all()

    assert db_session.get(User, target_id) is None
    for model in (AccessGrant, ChatMessage, BookingSlip):
        assert db_session.execute(select(model).where(model.user_id == target_id)).first() is None
    # The code they were assigned is revoked and no longer names them.
    db_session.refresh(assigned)
    assert (assigned.status, assigned.assigned_user_id, assigned.assigned_email) == ("revoked", None, None)
    # The code they "created" now belongs to the deleting admin.
    assert db_session.execute(select(AccessCode).where(AccessCode.created_by_user_id == target_id)).first() is None
    db_session.refresh(referred)
    assert referred.referred_by_user_id is None
    # The audit trail keeps the action, without them in it.
    old = db_session.execute(select(AuditLog).where(AuditLog.action == "access.payment_reported")).scalar_one()
    assert (old.actor_user_id, old.actor_email, old.target_user_id) == (None, None, None)
    entry = db_session.execute(select(AuditLog).where(AuditLog.action == "user.deleted")).scalar_one()
    assert entry.detail == {"user_id": target_id, "role": "user"}
    assert "gone@example.com" not in str(entry.detail)

    # The email is free to register again.
    assert db_session.execute(select(User).where(User.email == "gone@example.com")).first() is None


def test_cannot_delete_yourself_or_a_superadmin(db_session):
    admin = _make_user(db_session, "admin@example.com", role="superadmin")
    other_admin = _make_user(db_session, "admin2@example.com", role="superadmin")
    assert client.delete(f"/api/admin/users/{admin.id}", headers=_headers(admin)).status_code == 400
    assert client.delete(f"/api/admin/users/{other_admin.id}", headers=_headers(admin)).status_code == 400
    assert client.delete("/api/admin/users/999999", headers=_headers(admin)).status_code == 404


def test_only_a_superadmin_can_delete(db_session):
    plain = _make_user(db_session, "plain@example.com")
    victim = _make_user(db_session, "victim@example.com")
    assert client.delete(f"/api/admin/users/{victim.id}", headers=_headers(plain)).status_code == 403
    db_session.expire_all()
    assert db_session.get(User, victim.id) is not None
