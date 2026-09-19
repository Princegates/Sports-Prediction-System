"""The Dashboard notice: a superadmin-authored banner for every signed-in account.

Reuses the existing settings registry rather than a bespoke table -- two
settings (notice_enabled, notice_message) an admin edits like any other. The
read endpoint itself stays public/unauthenticated (cheap to serve, nothing
sensitive in it) even though the frontend only renders the banner on the
Dashboard, which requires being signed in.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.auth.passwords import hash_password
from app.auth.tokens import create_token
from app.config import get_settings
from app.db.models import User
from app.main import app

client = TestClient(app)


def _admin(db) -> User:
    user = User(email="notice-admin@example.com", name="Admin", password_hash=hash_password("a-good-password"),
                role="superadmin", status="active")
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _headers(user: User) -> dict:
    settings = get_settings()
    token = create_token({"user_id": user.id, "role": user.role}, settings.secret_key, settings.session_ttl_seconds)
    return {"Authorization": f"Bearer {token}"}


def test_notice_is_off_by_default_with_no_auth_needed(db_session):
    response = client.get("/api/public/notice")
    assert response.status_code == 200
    assert response.json() == {"enabled": False, "message": ""}


def test_admin_can_turn_on_a_notice_and_the_public_endpoint_reflects_it(db_session):
    admin = _admin(db_session)
    patch = client.patch(
        "/api/admin/settings",
        json={"values": {"notice_enabled": True, "notice_message": "Turkish Süper Lig is now live."}},
        headers=_headers(admin),
    )
    assert patch.status_code == 200, patch.text

    response = client.get("/api/public/notice")
    assert response.status_code == 200
    assert response.json() == {"enabled": True, "message": "Turkish Süper Lig is now live."}


def test_a_blank_message_never_reads_as_enabled(db_session):
    """Flipping the switch on with nothing to say would show an empty
    banner -- worse than no banner, since it looks like a rendering bug."""

    admin = _admin(db_session)
    client.patch("/api/admin/settings", json={"values": {"notice_enabled": True}}, headers=_headers(admin))

    response = client.get("/api/public/notice")
    assert response.json()["enabled"] is False


def test_only_a_superadmin_can_change_the_notice(db_session):
    plain = User(email="member@example.com", name="Member", password_hash=hash_password("a-good-password"),
                 role="user", status="active")
    db_session.add(plain)
    db_session.commit()
    db_session.refresh(plain)

    response = client.patch(
        "/api/admin/settings",
        json={"values": {"notice_enabled": True, "notice_message": "hi"}},
        headers=_headers(plain),
    )
    assert response.status_code == 403
