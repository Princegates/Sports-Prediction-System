"""A brand-new account gets full access automatically, no code needed --
then reverts to the free tier once the trial runs out, same as any other
expired grant. This is the "first sign-up is premium, later sign-ins are
basic until you redeem a code" behavior.
"""

from __future__ import annotations

import datetime as dt

from fastapi.testclient import TestClient

from app.auth.passwords import hash_password
from app.auth.tokens import create_token
from app.config import get_settings
from app.db.models import AccessGrant, User
from app.main import app

client = TestClient(app)


def _admin(db) -> User:
    user = User(email="trial-admin@example.com", name="Admin", password_hash=hash_password("a-good-password"),
                role="superadmin", status="active")
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _headers(user: User) -> dict:
    settings = get_settings()
    token = create_token({"user_id": user.id, "role": user.role}, settings.secret_key, settings.session_ttl_seconds)
    return {"Authorization": f"Bearer {token}"}


def test_registering_grants_immediate_access(db_session):
    register = client.post(
        "/api/auth/register",
        json={"email": "new.member@example.com", "name": "New Member", "password": "a-good-password"},
    )
    assert register.status_code == 200, register.text
    assert "full access for the next 1 day" in register.json()["message"]

    login = client.post("/api/auth/login", json={"email": "new.member@example.com", "password": "a-good-password"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    status = client.get("/api/access/status", headers=headers)
    assert status.status_code == 200
    body = status.json()
    assert body["has_access"] is True
    assert body["status"] == "active"

    # Roughly a day out, not indefinite.
    expires_at = dt.datetime.fromisoformat(body["expires_at"])
    assert dt.timedelta(hours=23) < (expires_at - dt.datetime.utcnow()) < dt.timedelta(hours=25)


def test_the_trial_length_follows_the_admin_setting(db_session):
    admin = _admin(db_session)
    client.patch("/api/admin/settings", json={"values": {"trial_duration_days": 7}}, headers=_headers(admin))

    register = client.post(
        "/api/auth/register",
        json={"email": "week-trial@example.com", "name": "Week Trial", "password": "a-good-password"},
    )
    assert "full access for the next 7 days" in register.json()["message"]

    login = client.post("/api/auth/login", json={"email": "week-trial@example.com", "password": "a-good-password"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    body = client.get("/api/access/status", headers=headers).json()
    expires_at = dt.datetime.fromisoformat(body["expires_at"])
    assert dt.timedelta(days=6, hours=23) < (expires_at - dt.datetime.utcnow()) < dt.timedelta(days=7, hours=1)


def test_turning_the_trial_off_leaves_new_signups_on_the_free_tier(db_session):
    admin = _admin(db_session)
    client.patch("/api/admin/settings", json={"values": {"trial_enabled": False}}, headers=_headers(admin))

    register = client.post(
        "/api/auth/register",
        json={"email": "no.trial@example.com", "name": "No Trial", "password": "a-good-password"},
    )
    assert register.status_code == 200
    assert "redeem your access code" in register.json()["message"]
    assert "full access" not in register.json()["message"]

    login = client.post("/api/auth/login", json={"email": "no.trial@example.com", "password": "a-good-password"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    body = client.get("/api/access/status", headers=headers).json()
    assert body["has_access"] is False


def test_a_lapsed_trial_reverts_to_the_free_tier_on_the_next_sign_in(db_session):
    """The behavior this feature is for: the first session is premium: once
    the trial's time is up, a later sign-in reads as basic again, exactly
    like any other expired grant -- no separate code path to get this wrong."""

    register = client.post(
        "/api/auth/register",
        json={"email": "lapsed@example.com", "name": "Lapsed", "password": "a-good-password"},
    )
    assert register.status_code == 200

    user = db_session.query(User).filter_by(email="lapsed@example.com").one()
    grant = db_session.query(AccessGrant).filter_by(user_id=user.id).one()
    grant.expires_at = dt.datetime.utcnow() - dt.timedelta(hours=1)
    db_session.commit()

    login = client.post("/api/auth/login", json={"email": "lapsed@example.com", "password": "a-good-password"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    body = client.get("/api/access/status", headers=headers).json()
    assert body["has_access"] is False
    assert body["status"] == "expired"

    # And the premium surface actually enforces it, not just the status read.
    assert client.get("/api/matches", headers=headers).status_code == 403
    # The free tier keeps working regardless.
    assert client.get("/api/predictions/free-picks", headers=headers).status_code == 200
