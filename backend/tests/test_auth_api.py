from fastapi.testclient import TestClient

from app.auth.passwords import hash_password
from app.db.models import User
from tests.test_api import _seed_league


def _make_superadmin(db_session) -> User:
    admin = User(email="admin@example.com", name="Admin", password_hash=hash_password("admin-password-1"), role="superadmin", status="active")
    db_session.add(admin)
    db_session.commit()
    db_session.refresh(admin)
    return admin


def test_register_creates_pending_user(db_session):
    from app.main import app

    client = TestClient(app)
    response = client.post(
        "/api/auth/register",
        json={"email": "New.User@Example.com", "name": "New User", "password": "a-good-password", "payment_reference": "MOMO-123"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["user"]["status"] == "pending"
    assert body["user"]["email"] == "new.user@example.com"  # normalized to lowercase


def test_register_rejects_short_password(db_session):
    from app.main import app

    client = TestClient(app)
    response = client.post("/api/auth/register", json={"email": "a@b.com", "name": "A", "password": "short"})
    assert response.status_code == 400


def test_register_rejects_duplicate_email(db_session):
    from app.main import app

    client = TestClient(app)
    payload = {"email": "dup@example.com", "name": "Dup", "password": "a-good-password"}
    assert client.post("/api/auth/register", json=payload).status_code == 200
    assert client.post("/api/auth/register", json=payload).status_code == 409


def test_login_rejects_pending_user(db_session):
    from app.main import app

    client = TestClient(app)
    payload = {"email": "pending@example.com", "name": "Pending", "password": "a-good-password"}
    client.post("/api/auth/register", json=payload)

    response = client.post("/api/auth/login", json={"email": payload["email"], "password": payload["password"]})
    assert response.status_code == 403


def test_login_rejects_wrong_password(db_session):
    from app.main import app

    client = TestClient(app)
    payload = {"email": "wrongpw@example.com", "name": "X", "password": "a-good-password"}
    client.post("/api/auth/register", json=payload)

    response = client.post("/api/auth/login", json={"email": payload["email"], "password": "not-the-password"})
    assert response.status_code == 401


def test_full_register_approve_login_flow(db_session):
    from app.main import app

    admin = _make_superadmin(db_session)
    client = TestClient(app)

    register_resp = client.post(
        "/api/auth/register",
        json={"email": "flow@example.com", "name": "Flow User", "password": "a-good-password", "payment_reference": "MOMO-999"},
    )
    user_id = register_resp.json()["user"]["id"]

    # Can't log in yet.
    assert client.post("/api/auth/login", json={"email": "flow@example.com", "password": "a-good-password"}).status_code == 403

    # Superadmin logs in and approves.
    admin_login = client.post("/api/auth/login", json={"email": admin.email, "password": "admin-password-1"})
    assert admin_login.status_code == 200
    admin_headers = {"Authorization": f"Bearer {admin_login.json()['access_token']}"}

    approve_resp = client.post(f"/api/admin/users/{user_id}/approve", json={}, headers=admin_headers)
    assert approve_resp.status_code == 200
    assert approve_resp.json()["status"] == "active"
    assert approve_resp.json()["approved_by_user_id"] == admin.id

    # Now the user can log in and access a protected endpoint.
    login_resp = client.post("/api/auth/login", json={"email": "flow@example.com", "password": "a-good-password"})
    assert login_resp.status_code == 200
    user_headers = {"Authorization": f"Bearer {login_resp.json()['access_token']}"}

    me_resp = client.get("/api/auth/me", headers=user_headers)
    assert me_resp.status_code == 200
    assert me_resp.json()["status"] == "active"


def test_non_superadmin_cannot_access_admin_routes(db_session, auth_headers):
    from app.main import app

    client = TestClient(app)
    response = client.get("/api/admin/users", headers=auth_headers)
    assert response.status_code == 403


def test_superadmin_cannot_suspend_self(db_session):
    from app.main import app

    admin = _make_superadmin(db_session)
    client = TestClient(app)
    login_resp = client.post("/api/auth/login", json={"email": admin.email, "password": "admin-password-1"})
    headers = {"Authorization": f"Bearer {login_resp.json()['access_token']}"}

    response = client.post(f"/api/admin/users/{admin.id}/suspend", headers=headers)
    assert response.status_code == 400


def test_preferences_persist_to_the_users_own_account(db_session, auth_headers):
    """Each user's theme/color-profile choice is their own -- stored on
    their account, not shared with other users or other browsers."""
    from app.main import app

    client = TestClient(app)
    response = client.patch("/api/auth/preferences", json={"theme": "light", "accent_profile": "fuchsia"}, headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["theme"] == "light"
    assert response.json()["accent_profile"] == "fuchsia"

    me_resp = client.get("/api/auth/me", headers=auth_headers)
    assert me_resp.json()["theme"] == "light"
    assert me_resp.json()["accent_profile"] == "fuchsia"


def test_match_history_is_per_user(db_session, auth_headers):
    """Viewing a match records it to the current user's own history, and a
    different user's history stays empty -- histories never leak between
    accounts."""
    from app.main import app

    _, upcoming = _seed_league(db_session)
    other = User(email="other@example.com", name="Other", password_hash=hash_password("a-good-password"), role="user", status="active")
    db_session.add(other)
    db_session.commit()

    from app.auth.tokens import create_token
    from app.config import get_settings

    settings = get_settings()
    other_headers = {"Authorization": f"Bearer {create_token({'user_id': other.id, 'role': other.role}, settings.secret_key, settings.session_ttl_seconds)}"}

    client = TestClient(app)
    assert client.post(f"/api/auth/history/{upcoming.id}", headers=auth_headers).status_code == 204

    history = client.get("/api/auth/history", headers=auth_headers).json()
    assert len(history) == 1
    assert history[0]["match"]["id"] == upcoming.id

    other_history = client.get("/api/auth/history", headers=other_headers).json()
    assert other_history == []

    # Viewing the same match again upserts rather than duplicating.
    client.post(f"/api/auth/history/{upcoming.id}", headers=auth_headers)
    assert len(client.get("/api/auth/history", headers=auth_headers).json()) == 1
