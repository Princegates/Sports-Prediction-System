from fastapi.testclient import TestClient

from app.auth.passwords import hash_password
from app.db.models import User


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
