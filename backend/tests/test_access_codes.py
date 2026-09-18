"""Tests for the access-code system: generation, redemption, and the
login-vs-feature-access split it sits behind.

There is no in-app payment gateway -- a superadmin generates a code after
confirming payment out of band, and a user redeems it to unlock the
prediction-serving routes. ``conftest.grant_active_access`` is used here
only to set up a *pre-existing* grant for a scenario (e.g. "already has
access, redeems again"); the redemption path itself always goes through the
real ``/api/access/redeem`` endpoint.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app.auth.passwords import hash_password
from app.auth.tokens import create_token
from app.config import get_settings
from app.db.models import AccessCode, AccessGrant, AccessRedemption, AuditLog, User
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


def _create_code(admin: User, **overrides) -> dict:
    payload = {"duration_days": 30, "redemption_limit": 1}
    payload.update(overrides)
    response = client.post("/api/admin/access-codes", json=payload, headers=_headers(admin))
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture()
def admin(db_session):
    return _make_user(db_session, "codeadmin@example.com", role="superadmin")


# --- Generation --------------------------------------------------------


def test_admin_can_generate_a_code(db_session, admin):
    body = _create_code(admin)
    assert "-" in body["code"]
    assert "*" not in body["code"]  # the one response where the real code is shown
    assert body["status"] == "active"
    assert body["redemption_count"] == 0


def test_generation_is_superadmin_only(db_session, admin):
    plain = _make_user(db_session, "plain@example.com")
    response = client.post("/api/admin/access-codes", json={"duration_days": 30}, headers=_headers(plain))
    assert response.status_code == 403


def test_listed_and_revoked_codes_are_masked(db_session, admin):
    created = _create_code(admin)

    listed = client.get("/api/admin/access-codes", headers=_headers(admin)).json()
    row = next(c for c in listed if c["id"] == created["id"])
    assert row["code"] != created["code"]
    assert row["code"].endswith(created["code"][-4:])
    assert "*" in row["code"]


def test_revoking_a_code_is_superadmin_only_and_idempotently_rejected(db_session, admin):
    plain = _make_user(db_session, "plain-revoke@example.com")
    code = _create_code(admin)

    assert client.post(f"/api/admin/access-codes/{code['id']}/revoke", json={}, headers=_headers(plain)).status_code == 403

    first = client.post(f"/api/admin/access-codes/{code['id']}/revoke", json={}, headers=_headers(admin))
    assert first.status_code == 200
    assert first.json()["status"] == "revoked"

    second = client.post(f"/api/admin/access-codes/{code['id']}/revoke", json={}, headers=_headers(admin))
    assert second.status_code == 400


# --- Redemption ----------------------------------------------------------


def test_redeeming_a_valid_code_unlocks_access(db_session, admin, headers_no_access):
    code = _create_code(admin)

    assert client.get("/api/teams", headers=headers_no_access).status_code == 403

    redeem_resp = client.post("/api/access/redeem", json={"code": code["code"]}, headers=headers_no_access)
    assert redeem_resp.status_code == 200
    assert redeem_resp.json()["status"] == "active"

    assert client.get("/api/teams", headers=headers_no_access).status_code == 200


def test_redemption_creates_grant_redemption_and_audit_rows(db_session, admin, user_no_access, headers_no_access):
    code = _create_code(admin)
    client.post("/api/access/redeem", json={"code": code["code"]}, headers=headers_no_access)

    access_code = db_session.query(AccessCode).filter(AccessCode.id == code["id"]).one()
    assert access_code.redemption_count == 1

    grant = db_session.query(AccessGrant).filter(AccessGrant.user_id == user_no_access.id).one()
    assert grant.status == "active"
    assert grant.expires_at > dt.datetime.utcnow()

    redemption = db_session.query(AccessRedemption).filter(AccessRedemption.access_code_id == access_code.id).one()
    assert redemption.user_id == user_no_access.id
    assert redemption.grant_id == grant.id

    entry = db_session.query(AuditLog).filter(AuditLog.action == "access_code.redeemed").one()
    assert entry.target_user_id == user_no_access.id


def test_redeeming_an_unknown_code_is_rejected(db_session, headers_no_access):
    response = client.post("/api/access/redeem", json={"code": "NOPE-NOPE-NOPE"}, headers=headers_no_access)
    assert response.status_code == 400


def test_redeeming_a_revoked_code_is_rejected(db_session, admin, headers_no_access):
    code = _create_code(admin)
    client.post(f"/api/admin/access-codes/{code['id']}/revoke", json={}, headers=_headers(admin))

    response = client.post("/api/access/redeem", json={"code": code["code"]}, headers=headers_no_access)
    assert response.status_code == 400
    assert "revoked" in response.json()["detail"].lower()


def test_redeeming_an_expired_code_is_rejected(db_session, admin, headers_no_access):
    code = _create_code(admin, code_expires_in_days=1)
    row = db_session.query(AccessCode).filter(AccessCode.id == code["id"]).one()
    row.code_expires_at = dt.datetime.utcnow() - dt.timedelta(days=1)
    db_session.commit()

    response = client.post("/api/access/redeem", json={"code": code["code"]}, headers=headers_no_access)
    assert response.status_code == 400
    assert "expired" in response.json()["detail"].lower()


def test_redeeming_past_the_limit_is_rejected(db_session, admin):
    code = _create_code(admin, redemption_limit=1)
    first = _make_user(db_session, "first@example.com")
    second = _make_user(db_session, "second@example.com")

    assert client.post("/api/access/redeem", json={"code": code["code"]}, headers=_headers(first)).status_code == 200
    response = client.post("/api/access/redeem", json={"code": code["code"]}, headers=_headers(second))
    assert response.status_code == 400
    assert "fully redeemed" in response.json()["detail"].lower()


def test_redeeming_the_same_code_twice_is_rejected(db_session, admin, headers_no_access):
    code = _create_code(admin, redemption_limit=5)
    client.post("/api/access/redeem", json={"code": code["code"]}, headers=headers_no_access)

    response = client.post("/api/access/redeem", json={"code": code["code"]}, headers=headers_no_access)
    assert response.status_code == 400
    assert "already redeemed" in response.json()["detail"].lower()


def test_code_assigned_to_another_account_is_rejected(db_session, admin, headers_no_access):
    other = _make_user(db_session, "assigned-target@example.com")
    code = _create_code(admin, assigned_user_email=other.email)

    response = client.post("/api/access/redeem", json={"code": code["code"]}, headers=headers_no_access)
    assert response.status_code == 400
    assert "assigned" in response.json()["detail"].lower()


def test_redeeming_while_still_active_extends_rather_than_replaces(db_session, admin):
    user = _make_user(db_session, "renewer@example.com")
    first_grant = grant_active_access(db_session, user, days=10)
    first_expiry = first_grant.expires_at

    code = _create_code(admin, duration_days=10)
    response = client.post("/api/access/redeem", json={"code": code["code"]}, headers=_headers(user))
    assert response.status_code == 200

    new_expiry = dt.datetime.fromisoformat(response.json()["expires_at"])
    # Stacked on top of the remaining time, not reset to "now + 10 days".
    assert new_expiry > first_expiry + dt.timedelta(days=9)


def test_access_status_endpoint_reports_none_and_active(db_session, headers_no_access, auth_headers):
    assert client.get("/api/access/status", headers=headers_no_access).json()["status"] == "none"
    assert client.get("/api/access/status", headers=auth_headers).json()["status"] == "active"


# --- Admin grant management ------------------------------------------------


def test_admin_can_extend_a_users_grant(db_session, admin):
    user = _make_user(db_session, "extend-me@example.com")
    grant = grant_active_access(db_session, user, days=5)
    original_expiry = grant.expires_at

    response = client.post(
        f"/api/admin/users/{user.id}/access/extend", json={"additional_days": 10}, headers=_headers(admin)
    )
    assert response.status_code == 200
    assert response.json()["access_status"] == "active"

    db_session.refresh(grant)
    assert grant.expires_at > original_expiry


def test_extending_a_user_with_no_grant_is_rejected(db_session, admin):
    user = _make_user(db_session, "never-redeemed@example.com")
    response = client.post(
        f"/api/admin/users/{user.id}/access/extend", json={"additional_days": 10}, headers=_headers(admin)
    )
    assert response.status_code == 400


def test_admin_can_revoke_a_users_grant(db_session, admin):
    user = _make_user(db_session, "revoke-me@example.com")
    grant_active_access(db_session, user)

    response = client.post(
        f"/api/admin/users/{user.id}/access/revoke", json={"reason": "chargeback"}, headers=_headers(admin)
    )
    assert response.status_code == 200
    assert response.json()["access_status"] == "none"
    assert client.get("/api/access/status", headers=_headers(user)).json()["status"] == "none"


def test_extend_and_revoke_grant_are_superadmin_only(db_session, admin):
    """A plain user can't manage grants at all -- neither their own nor
    anyone else's -- since these are admin-only routes, checked before any
    grant logic runs."""

    plain = _make_user(db_session, "plain2@example.com")
    target = _make_user(db_session, "target@example.com")
    grant_active_access(db_session, target)

    assert (
        client.post(
            f"/api/admin/users/{target.id}/access/extend", json={"additional_days": 5}, headers=_headers(plain)
        ).status_code
        == 403
    )
    assert (
        client.post(f"/api/admin/users/{target.id}/access/revoke", json={}, headers=_headers(plain)).status_code
        == 403
    )
    # Even against their own account.
    assert (
        client.post(
            f"/api/admin/users/{plain.id}/access/extend", json={"additional_days": 365}, headers=_headers(plain)
        ).status_code
        == 403
    )


# --- Superadmin bypass ------------------------------------------------------


def test_superadmin_never_needs_a_redeemed_code(db_session, admin):
    """Managing the platform is the superadmin's job description -- gating it
    on the same code they themselves would have to generate would be circular."""

    assert client.get("/api/teams", headers=_headers(admin)).status_code == 200


# --- Admin settings ----------------------------------------------------------


def test_admin_settings_default_to_sane_values_and_are_superadmin_only(db_session, admin):
    plain = _make_user(db_session, "settings-plain@example.com")
    assert client.get("/api/admin/settings", headers=_headers(plain)).status_code == 403

    response = client.get("/api/admin/settings", headers=_headers(admin))
    assert response.status_code == 200
    body = response.json()
    assert body["default_duration_days"] == 30
    assert body["default_redemption_limit"] == 1


def test_admin_can_update_settings_and_it_persists(db_session, admin):
    response = client.patch(
        "/api/admin/settings",
        json={"default_duration_days": 14, "default_redemption_limit": 5},
        headers=_headers(admin),
    )
    assert response.status_code == 200
    assert response.json()["default_duration_days"] == 14
    assert response.json()["default_redemption_limit"] == 5

    refetched = client.get("/api/admin/settings", headers=_headers(admin)).json()
    assert refetched["default_duration_days"] == 14
    assert refetched["default_redemption_limit"] == 5


def test_admin_settings_reject_non_positive_values(db_session, admin):
    response = client.patch(
        "/api/admin/settings",
        json={"default_duration_days": 0, "default_redemption_limit": 1},
        headers=_headers(admin),
    )
    assert response.status_code == 400


def test_updating_settings_is_superadmin_only(db_session, admin):
    plain = _make_user(db_session, "settings-plain-2@example.com")
    response = client.patch(
        "/api/admin/settings",
        json={"default_duration_days": 14, "default_redemption_limit": 1},
        headers=_headers(plain),
    )
    assert response.status_code == 403
