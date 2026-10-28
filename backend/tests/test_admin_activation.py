"""Members who haven't confirmed their email never got their free trial. The
admin needs to see them, resend the link, or activate them by hand."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import app_settings, mailer
from app.access import issue_signup_trial
from app.auth.passwords import hash_password
from app.auth.tokens import create_token
from app.config import get_settings
from app.db.models import AccessCode, AuditLog, User
from app.main import app
from app.timeutil import utcnow

client = TestClient(app)


def _user(db, email, role="user", verified=False):
    user = User(
        email=email, name=email.split("@")[0], password_hash=hash_password("a-good-password"),
        role=role, status="active", email_verified_at=utcnow() if verified else None,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _headers(user):
    s = get_settings()
    return {"Authorization": f"Bearer {create_token({'user_id': user.id, 'role': user.role}, s.secret_key, s.session_ttl_seconds)}"}


@pytest.fixture()
def admin(db_session):
    return _user(db_session, "admin@example.com", role="superadmin", verified=True)


@pytest.fixture()
def email_ready(db_session, monkeypatch):
    """Email configured with a public site URL, and sending recorded."""

    app_settings.set_values(db_session, {"public_site_url": "https://example.test", "trial_duration_days": 5})
    sent: list[tuple[str, str, str]] = []
    monkeypatch.setattr(mailer, "is_configured", lambda db=None: True)
    monkeypatch.setattr(
        mailer, "send_email",
        lambda to, subject, body, db=None: (sent.append((to, subject, body)), mailer.SendResult(sent=True))[1],
    )
    return sent


def _row(admin, email):
    return next(u for u in client.get("/api/admin/users", headers=_headers(admin)).json() if u["email"] == email)


def test_an_unconfirmed_member_is_flagged_and_counted(db_session, admin):
    _user(db_session, "waiting@example.com")
    _user(db_session, "done@example.com", verified=True)

    assert _row(admin, "waiting@example.com")["activation_pending"] is True
    assert _row(admin, "done@example.com")["activation_pending"] is False
    assert _row(admin, "admin@example.com")["activation_pending"] is False
    assert client.get("/api/admin/overview", headers=_headers(admin)).json()["unverified_users"] == 1


def test_a_member_who_already_had_a_trial_is_not_pending(db_session, admin):
    """Accounts from before confirmation existed have no confirmation date but
    did get their trial -- there's nothing left to activate."""

    old = _user(db_session, "old@example.com")
    issue_signup_trial(db_session, old, duration_days=5)

    assert _row(admin, "old@example.com")["activation_pending"] is False
    assert client.get("/api/admin/overview", headers=_headers(admin)).json()["unverified_users"] == 0


def test_nobody_is_pending_when_confirmation_is_switched_off(db_session, admin):
    _user(db_session, "waiting@example.com")
    app_settings.set_values(db_session, {"email_verification_required": False})

    assert _row(admin, "waiting@example.com")["activation_pending"] is False
    assert client.get("/api/admin/overview", headers=_headers(admin)).json()["unverified_users"] == 0


def test_resend_emails_a_fresh_confirmation_link(db_session, admin, email_ready):
    member = _user(db_session, "waiting@example.com")

    response = client.post(f"/api/admin/users/{member.id}/resend-verification", headers=_headers(admin))

    assert response.status_code == 204
    to, _, body = email_ready[0]
    assert to == "waiting@example.com" and "https://example.test/verify-email?token=" in body
    assert db_session.query(AuditLog).filter(AuditLog.action == "account.verification_resent").count() == 1


def test_resend_is_refused_for_someone_already_confirmed(db_session, admin, email_ready):
    member = _user(db_session, "done@example.com", verified=True)
    assert client.post(f"/api/admin/users/{member.id}/resend-verification", headers=_headers(admin)).status_code == 400
    assert email_ready == []


def test_resend_explains_when_email_is_not_set_up(db_session, admin):
    member = _user(db_session, "waiting@example.com")
    response = client.post(f"/api/admin/users/{member.id}/resend-verification", headers=_headers(admin))
    assert response.status_code == 400 and "Settings" in response.json()["detail"]


def test_resend_is_rate_limited_per_member(db_session, admin, email_ready):
    member = _user(db_session, "waiting@example.com")
    codes = [client.post(f"/api/admin/users/{member.id}/resend-verification", headers=_headers(admin)).status_code for _ in range(4)]
    assert codes == [204, 204, 204, 429]


def test_activate_confirms_the_email_and_starts_the_trial(db_session, admin, email_ready):
    member = _user(db_session, "waiting@example.com")

    body = client.post(f"/api/admin/users/{member.id}/activate", headers=_headers(admin)).json()

    assert body["activation_pending"] is False and body["access_status"] == "active"
    db_session.expire_all()
    assert db_session.get(User, member.id).email_verified_at is not None
    assert db_session.query(AccessCode).filter(AccessCode.assigned_user_id == member.id, AccessCode.kind == "trial").count() == 1
    assert db_session.query(AuditLog).filter(AuditLog.action == "account.activated_by_admin").count() == 1


def test_activate_does_not_start_a_second_trial_for_someone_who_had_one(db_session, admin, email_ready):
    old = _user(db_session, "old@example.com")
    issue_signup_trial(db_session, old, duration_days=5)

    client.post(f"/api/admin/users/{old.id}/activate", headers=_headers(admin))

    assert db_session.query(AccessCode).filter(AccessCode.assigned_user_id == old.id, AccessCode.kind == "trial").count() == 1


def test_activate_is_refused_twice_and_for_admins(db_session, admin, email_ready):
    member = _user(db_session, "waiting@example.com")
    assert client.post(f"/api/admin/users/{member.id}/activate", headers=_headers(admin)).status_code == 200
    assert client.post(f"/api/admin/users/{member.id}/activate", headers=_headers(admin)).status_code == 400
    assert client.post(f"/api/admin/users/{admin.id}/activate", headers=_headers(admin)).status_code == 400


def test_members_cannot_use_these_tools(db_session, admin, email_ready):
    member = _user(db_session, "waiting@example.com")
    other = _user(db_session, "other@example.com")
    assert client.post(f"/api/admin/users/{member.id}/activate", headers=_headers(other)).status_code == 403
    assert client.post(f"/api/admin/users/{member.id}/resend-verification", headers=_headers(other)).status_code == 403
