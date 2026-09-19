"""Tests for the admin-side safeguards around account status and access.

Account status (active/suspended) and feature access (a redeemed
AccessGrant) are two separate gates now, so the failure modes worth testing
are the ones that would break either gate open or lock it shut permanently.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api import rate_limit
from app.auth.passwords import hash_password
from app.auth.tokens import create_token
from app.config import get_settings
from app.db.models import AuditLog, User
from app.main import app
from tests.conftest import grant_active_access

client = TestClient(app)


def _make_user(db, email: str, role: str = "user", status: str = "active") -> User:
    user = User(
        email=email,
        name=email.split("@")[0],
        password_hash=hash_password("a-good-password"),
        role=role,
        status=status,
    )
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
    return _make_user(db_session, "admin@example.com", role="superadmin")


# --- Overview -------------------------------------------------------------


def test_overview_surfaces_users_without_access(db_session, admin):
    granted = _make_user(db_session, "granted@example.com")
    grant_active_access(db_session, granted)
    _make_user(db_session, "no-access-1@example.com")
    _make_user(db_session, "no-access-2@example.com")
    _make_user(db_session, "s1@example.com", status="suspended")

    body = client.get("/api/admin/overview", headers=_headers(admin)).json()
    assert body["suspended_users"] == 1
    assert body["active_users"] == 4  # admin, granted, and the two without access
    assert body["superadmins"] == 1
    assert body["total_users"] == 5
    assert body["active_access_grants"] == 1
    assert body["users_without_access"] == 3  # admin + the two without access


def test_overview_requires_superadmin(db_session, admin):
    plain = _make_user(db_session, "plain@example.com")
    assert client.get("/api/admin/overview", headers=_headers(plain)).status_code == 403
    assert client.get("/api/admin/overview").status_code == 401


# --- Audit log ------------------------------------------------------------


def test_audit_log_keeps_the_full_sequence_not_just_the_latest_state(db_session, admin):
    """Each status change is recorded on its own -- there's no single column
    that only holds the latest state, so the audit log is what makes the
    full sequence recoverable afterwards."""

    target = _make_user(db_session, "churn@example.com", status="suspended")
    headers = _headers(admin)

    client.post(f"/api/admin/users/{target.id}/reinstate", headers=headers)
    client.post(f"/api/admin/users/{target.id}/suspend", headers=headers)
    client.post(f"/api/admin/users/{target.id}/reinstate", headers=headers)

    actions = [
        row.action
        for row in db_session.query(AuditLog)
        .filter(AuditLog.target_user_id == target.id)
        .order_by(AuditLog.id.asc())
    ]
    assert actions == ["user.reinstated", "user.suspended", "user.reinstated"]


def test_registration_is_audited(db_session):
    rate_limit.reset()
    response = client.post(
        "/api/auth/register",
        json={"email": "newcomer@example.com", "name": "Newcomer", "password": "a-good-password"},
    )
    assert response.status_code == 200

    entry = db_session.query(AuditLog).filter(AuditLog.action == "account.registered").one()
    assert entry.actor_email == "newcomer@example.com"


def test_audit_log_endpoint_is_superadmin_only(db_session, admin):
    plain = _make_user(db_session, "nosy@example.com")
    assert client.get("/api/admin/audit-log", headers=_headers(plain)).status_code == 403
    assert client.get("/api/admin/audit-log", headers=_headers(admin)).status_code == 200


# --- Reinstatement --------------------------------------------------------


def test_suspension_is_reversible(db_session, admin):
    target = _make_user(db_session, "oops@example.com", status="suspended")
    response = client.post(f"/api/admin/users/{target.id}/reinstate", headers=_headers(admin))
    assert response.status_code == 200
    assert response.json()["status"] == "active"


def test_reinstating_an_active_account_is_rejected(db_session, admin):
    target = _make_user(db_session, "fine@example.com", status="active")
    response = client.post(f"/api/admin/users/{target.id}/reinstate", headers=_headers(admin))
    assert response.status_code == 400


# --- Lockout protection ---------------------------------------------------


def test_api_cannot_reach_a_state_with_zero_superadmins(db_session, admin):
    """The invariant that matters: no sequence of demotions over this API can
    leave nobody able to approve registrations.

    Two guards enforce it together -- you cannot demote yourself, and you
    cannot demote the last superadmin. Only the first is reachable here (the
    caller is always a superadmin, so demoting someone else leaves at least
    the caller standing), which is exactly why the test asserts the invariant
    rather than one guard's error message.
    """

    second = _make_user(db_session, "second@example.com", role="superadmin")
    third = _make_user(db_session, "third@example.com", role="superadmin")

    # Demote everyone we're allowed to, from every available actor.
    for actor in (admin, second, third):
        for target in (admin, second, third):
            client.post(f"/api/admin/users/{target.id}/demote", headers=_headers(actor))

    remaining = db_session.query(User).filter(User.role == "superadmin").count()
    assert remaining >= 1


def test_cannot_demote_the_last_superadmin_guard_holds_directly(db_session, admin):
    """The last-superadmin guard itself, exercised at the function level since
    the endpoint's self-demotion check shadows it. This is what protects the
    invariant if a script or a future endpoint demotes on someone's behalf."""

    from fastapi import HTTPException

    from app.api.routes_admin import demote_user

    with pytest.raises(HTTPException) as exc:
        # An actor other than the target, where the target is nonetheless the
        # only superadmin -- a state the HTTP layer can't produce.
        plain_actor = _make_user(db_session, "scripted@example.com", role="user")
        demote_user(user_id=admin.id, admin=plain_actor, db=db_session)

    assert exc.value.status_code == 400
    assert "only superadmin" in exc.value.detail


def test_cannot_suspend_or_demote_yourself(db_session, admin):
    headers = _headers(admin)
    assert client.post(f"/api/admin/users/{admin.id}/suspend", headers=headers).status_code == 400
    assert client.post(f"/api/admin/users/{admin.id}/demote", headers=headers).status_code == 400


# --- Account status endpoint ---------------------------------------------


def test_account_status_reports_active_during_the_signup_trial(db_session):
    """A brand-new account's automatic trial (see tests/test_signup_trial.py)
    means this reports "active", not "no_access", right after registering."""

    rate_limit.reset()
    client.post(
        "/api/auth/register",
        json={"email": "waiting@example.com", "name": "Waiting", "password": "a-good-password"},
    )

    response = client.post(
        "/api/auth/status", json={"email": "waiting@example.com", "password": "a-good-password"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "active"

    # Login itself works right away regardless.
    assert client.post(
        "/api/auth/login", json={"email": "waiting@example.com", "password": "a-good-password"}
    ).status_code == 200


def test_account_status_reports_no_access_when_the_trial_is_off(db_session, admin):
    rate_limit.reset()
    settings = get_settings()
    admin_headers = {"Authorization": f"Bearer {create_token({'user_id': admin.id, 'role': admin.role}, settings.secret_key, settings.session_ttl_seconds)}"}
    client.patch("/api/admin/settings", json={"values": {"trial_enabled": False}}, headers=admin_headers)

    client.post(
        "/api/auth/register",
        json={"email": "no-trial-status@example.com", "name": "No Trial", "password": "a-good-password"},
    )

    response = client.post(
        "/api/auth/status", json={"email": "no-trial-status@example.com", "password": "a-good-password"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "no_access"


def test_account_status_does_not_enumerate_accounts(db_session, admin):
    """An unknown email and a wrong password must both return the same
    ``no_access`` shape, or this endpoint becomes a way to discover which
    addresses are registered."""

    rate_limit.reset()
    unknown = client.post(
        "/api/auth/status", json={"email": "nobody@example.com", "password": "whatever-123"}
    ).json()
    wrong_password = client.post(
        "/api/auth/status", json={"email": "admin@example.com", "password": "wrong-password-123"}
    ).json()

    assert unknown["status"] == "no_access"
    assert wrong_password["status"] == "no_access"
    assert unknown["message"] == wrong_password["message"]
    assert unknown["submitted_at"] is None and wrong_password["submitted_at"] is None


# --- Rate limiting --------------------------------------------------------


def test_login_is_rate_limited(db_session, admin):
    settings = get_settings()
    rate_limit.reset()

    for _ in range(settings.login_rate_limit_attempts):
        client.post("/api/auth/login", json={"email": "admin@example.com", "password": "wrong-pass-123"})

    blocked = client.post(
        "/api/auth/login", json={"email": "admin@example.com", "password": "a-good-password"}
    )
    assert blocked.status_code == 429
    assert blocked.headers["Retry-After"]


def test_registration_is_rate_limited(db_session):
    settings = get_settings()
    rate_limit.reset()

    for i in range(settings.register_rate_limit_attempts):
        response = client.post(
            "/api/auth/register",
            json={"email": f"spam{i}@example.com", "name": "Spam", "password": "a-good-password"},
        )
        assert response.status_code == 200

    blocked = client.post(
        "/api/auth/register",
        json={"email": "spam-over@example.com", "name": "Spam", "password": "a-good-password"},
    )
    assert blocked.status_code == 429


def test_rejected_requests_do_not_extend_the_window(db_session, admin):
    """A client that keeps hammering a closed window must not push its own
    reset further out -- otherwise a determined attacker locks out the real
    user indefinitely."""

    rate_limit.reset()
    limit = 3
    for _ in range(limit):
        allowed, _ = rate_limit.check("test-key", limit, 60)
        assert allowed

    first_rejection = rate_limit.check("test-key", limit, 60)
    for _ in range(20):
        rate_limit.check("test-key", limit, 60)
    last_rejection = rate_limit.check("test-key", limit, 60)

    assert first_rejection[0] is False and last_rejection[0] is False
    # retry_after counts down rather than resetting on each rejected attempt.
    assert last_rejection[1] <= first_rejection[1]
