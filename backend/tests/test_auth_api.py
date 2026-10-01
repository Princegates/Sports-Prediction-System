import datetime as dt

from fastapi.testclient import TestClient

from app.auth.passwords import hash_password
from app.db.models import AuditLog, User
from tests.test_api import _seed_league

# Comfortably over the 18-year minimum, so a test exercising something else
# (a duplicate email, a short password) never trips the age gate instead.
ADULT_DOB = "1990-01-01"


def _make_superadmin(db_session) -> User:
    admin = User(email="admin@example.com", name="Admin", password_hash=hash_password("admin-password-1"), role="superadmin", status="active")
    db_session.add(admin)
    db_session.commit()
    db_session.refresh(admin)
    return admin


def test_register_creates_active_user(db_session):
    from app.main import app

    client = TestClient(app)
    response = client.post(
        "/api/auth/register",
        json={"email": "New.User@Example.com", "name": "New User", "password": "a-good-password", "date_of_birth": ADULT_DOB},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["user"]["status"] == "active"
    assert body["user"]["email"] == "new.user@example.com"  # normalized to lowercase


def test_register_rejects_short_password(db_session):
    from app.main import app

    client = TestClient(app)
    response = client.post(
        "/api/auth/register", json={"email": "a@b.com", "name": "A", "password": "short", "date_of_birth": ADULT_DOB}
    )
    assert response.status_code == 400


def test_register_rejects_someone_under_18(db_session):
    from app.main import app

    client = TestClient(app)
    seventeen_years_ago = dt.date.today().replace(year=dt.date.today().year - 17)
    response = client.post(
        "/api/auth/register",
        json={
            "email": "minor@example.com",
            "name": "Minor",
            "password": "a-good-password",
            "date_of_birth": seventeen_years_ago.isoformat(),
        },
    )
    assert response.status_code == 400
    assert "18" in response.json()["detail"]


def test_register_accepts_someone_who_turns_18_today(db_session):
    from app.main import app

    client = TestClient(app)
    today = dt.date.today()
    turns_18_today = today.replace(year=today.year - 18)
    response = client.post(
        "/api/auth/register",
        json={
            "email": "just-eighteen@example.com",
            "name": "Just Eighteen",
            "password": "a-good-password",
            "date_of_birth": turns_18_today.isoformat(),
        },
    )
    assert response.status_code == 200


def test_register_rejects_someone_who_turns_18_tomorrow(db_session):
    """One day short of 18 -- catches a naive year-subtraction age check,
    which would wrongly accept this."""
    from app.main import app

    client = TestClient(app)
    tomorrow = dt.date.today() + dt.timedelta(days=1)
    turns_18_tomorrow = tomorrow.replace(year=tomorrow.year - 18)
    response = client.post(
        "/api/auth/register",
        json={
            "email": "almost-eighteen@example.com",
            "name": "Almost Eighteen",
            "password": "a-good-password",
            "date_of_birth": turns_18_tomorrow.isoformat(),
        },
    )
    assert response.status_code == 400


def test_register_rejects_a_date_of_birth_in_the_future(db_session):
    from app.main import app

    client = TestClient(app)
    tomorrow = dt.date.today() + dt.timedelta(days=1)
    response = client.post(
        "/api/auth/register",
        json={
            "email": "time-traveler@example.com",
            "name": "Time Traveler",
            "password": "a-good-password",
            "date_of_birth": tomorrow.isoformat(),
        },
    )
    assert response.status_code == 400


def test_register_rejects_duplicate_email(db_session):
    from app.main import app

    client = TestClient(app)
    payload = {"email": "dup@example.com", "name": "Dup", "password": "a-good-password", "date_of_birth": ADULT_DOB}
    assert client.post("/api/auth/register", json=payload).status_code == 200
    assert client.post("/api/auth/register", json=payload).status_code == 409


def test_register_sends_a_welcome_email_when_mail_is_configured(db_session, monkeypatch):
    from app import mailer
    from app.main import app

    sent = []
    monkeypatch.setattr(mailer, "is_configured", lambda db=None: True)
    monkeypatch.setattr(
        mailer,
        "send_email",
        lambda to, subject, body, db=None: sent.append((to, subject)) or mailer.SendResult(sent=True),
    )

    client = TestClient(app)
    response = client.post(
        "/api/auth/register",
        json={"email": "Welcome.User@Example.com", "name": "Welcome User", "password": "a-good-password", "date_of_birth": ADULT_DOB},
    )

    assert response.status_code == 200
    assert sent == [("welcome.user@example.com", "Your account is ready")]


def test_register_does_not_call_send_email_when_mail_is_not_configured(db_session, monkeypatch):
    from app import mailer
    from app.main import app

    monkeypatch.setattr(mailer, "is_configured", lambda db=None: False)

    def fail(*args, **kwargs):
        raise AssertionError("send_email should not be called when mail is not configured")

    monkeypatch.setattr(mailer, "send_email", fail)

    client = TestClient(app)
    response = client.post(
        "/api/auth/register",
        json={"email": "no.mail@example.com", "name": "No Mail", "password": "a-good-password", "date_of_birth": ADULT_DOB},
    )
    assert response.status_code == 200


def test_login_succeeds_immediately_after_register(db_session):
    """There is no approval gate to clear any more -- a freshly registered
    account can log in right away. Whether it can reach predictions is a
    separate, access-grant question (see test_access_codes.py)."""
    from app.main import app

    client = TestClient(app)
    payload = {"email": "newcomer@example.com", "name": "Newcomer", "password": "a-good-password", "date_of_birth": ADULT_DOB}
    client.post("/api/auth/register", json=payload)

    response = client.post("/api/auth/login", json={"email": payload["email"], "password": payload["password"]})
    assert response.status_code == 200


def test_login_rejects_suspended_user(db_session):
    from app.main import app

    admin = _make_superadmin(db_session)
    client = TestClient(app)
    payload = {"email": "suspended@example.com", "name": "Suspended", "password": "a-good-password", "date_of_birth": ADULT_DOB}
    client.post("/api/auth/register", json=payload)

    admin_login = client.post("/api/auth/login", json={"email": admin.email, "password": "admin-password-1"})
    admin_headers = {"Authorization": f"Bearer {admin_login.json()['access_token']}"}

    users = client.get("/api/admin/users", headers=admin_headers).json()
    user_id = next(row["id"] for row in users if row["email"] == payload["email"])
    client.post(f"/api/admin/users/{user_id}/suspend", headers=admin_headers)

    response = client.post("/api/auth/login", json={"email": payload["email"], "password": payload["password"]})
    assert response.status_code == 403


def test_login_rejects_wrong_password(db_session):
    from app.main import app

    client = TestClient(app)
    payload = {"email": "wrongpw@example.com", "name": "X", "password": "a-good-password", "date_of_birth": ADULT_DOB}
    client.post("/api/auth/register", json=payload)

    response = client.post("/api/auth/login", json={"email": payload["email"], "password": "not-the-password"})
    assert response.status_code == 401


def test_successful_login_is_audit_logged(db_session):
    from app.main import app

    client = TestClient(app)
    payload = {"email": "audited-login@example.com", "name": "Audited", "password": "a-good-password", "date_of_birth": ADULT_DOB}
    client.post("/api/auth/register", json=payload)
    db_session.query(AuditLog).delete()  # drop the registration entry, isolate the login
    db_session.commit()

    response = client.post("/api/auth/login", json={"email": payload["email"], "password": payload["password"]})
    assert response.status_code == 200

    entry = db_session.query(AuditLog).filter(AuditLog.action == "account.login").one()
    assert entry.actor_email == payload["email"]
    assert entry.target_user_id is not None


def test_failed_login_is_audit_logged_even_for_an_unknown_email(db_session):
    from app.main import app

    client = TestClient(app)
    response = client.post(
        "/api/auth/login", json={"email": "nobody-here@example.com", "password": "whatever-123"}
    )
    assert response.status_code == 401

    entry = db_session.query(AuditLog).filter(AuditLog.action == "account.login_failed").one()
    assert entry.actor_email == "nobody-here@example.com"
    assert entry.actor_user_id is None


def test_suspended_login_attempt_is_audit_logged_as_blocked_not_failed(db_session):
    from app.main import app

    admin = _make_superadmin(db_session)
    client = TestClient(app)
    payload = {"email": "blocked-login@example.com", "name": "Blocked", "password": "a-good-password", "date_of_birth": ADULT_DOB}
    client.post("/api/auth/register", json=payload)

    admin_login = client.post("/api/auth/login", json={"email": admin.email, "password": "admin-password-1"})
    admin_headers = {"Authorization": f"Bearer {admin_login.json()['access_token']}"}
    users = client.get("/api/admin/users", headers=admin_headers).json()
    user_id = next(row["id"] for row in users if row["email"] == payload["email"])
    client.post(f"/api/admin/users/{user_id}/suspend", headers=admin_headers)

    response = client.post("/api/auth/login", json={"email": payload["email"], "password": payload["password"]})
    assert response.status_code == 403

    entry = db_session.query(AuditLog).filter(AuditLog.action == "account.login_blocked").one()
    assert entry.target_user_id == user_id
    assert entry.detail["reason"] == "suspended"


def test_full_register_login_trial_flow(db_session):
    """Registering and logging in no longer needs a superadmin in the loop,
    and the prediction surface isn't locked immediately either -- a
    brand-new account gets a short automatic trial (see test_signup_trial.py
    for the trial's own behavior, including what happens once it lapses).
    This just checks the boundary right after signing up."""
    from app.main import app

    client = TestClient(app)

    register_resp = client.post(
        "/api/auth/register",
        json={"email": "flow@example.com", "name": "Flow User", "password": "a-good-password", "date_of_birth": ADULT_DOB},
    )
    assert register_resp.json()["user"]["status"] == "active"

    # Logs in immediately -- no approval step to wait on.
    login_resp = client.post("/api/auth/login", json={"email": "flow@example.com", "password": "a-good-password"})
    assert login_resp.status_code == 200
    user_headers = {"Authorization": f"Bearer {login_resp.json()['access_token']}"}

    me_resp = client.get("/api/auth/me", headers=user_headers)
    assert me_resp.status_code == 200
    assert me_resp.json()["status"] == "active"

    # The trial unlocks the prediction surface right away.
    assert client.get("/api/teams", headers=user_headers).status_code == 200


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


def test_update_profile_changes_name(db_session, auth_headers):
    from app.main import app

    client = TestClient(app)
    response = client.patch("/api/auth/profile", json={"name": "New Name"}, headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["name"] == "New Name"

    me_resp = client.get("/api/auth/me", headers=auth_headers)
    assert me_resp.json()["name"] == "New Name"


def test_update_profile_name_change_is_audit_logged(db_session, auth_headers):
    from app.main import app

    client = TestClient(app)
    response = client.patch("/api/auth/profile", json={"name": "Renamed Person"}, headers=auth_headers)
    assert response.status_code == 200

    entry = db_session.query(AuditLog).filter(AuditLog.action == "account.profile_updated").one()
    assert entry.detail["to"] == "Renamed Person"


def test_update_profile_is_not_logged_when_the_name_does_not_change(db_session, auth_headers):
    from app.main import app

    client = TestClient(app)
    me = client.get("/api/auth/me", headers=auth_headers).json()
    response = client.patch("/api/auth/profile", json={"name": me["name"]}, headers=auth_headers)
    assert response.status_code == 200

    assert db_session.query(AuditLog).filter(AuditLog.action == "account.profile_updated").count() == 0


def test_update_profile_rejects_empty_name(db_session, auth_headers):
    from app.main import app

    client = TestClient(app)
    response = client.patch("/api/auth/profile", json={"name": "   "}, headers=auth_headers)
    assert response.status_code == 400


def test_change_password_requires_correct_current_password(db_session, auth_headers):
    from app.main import app

    client = TestClient(app)
    response = client.patch(
        "/api/auth/password",
        json={"current_password": "wrong-password", "new_password": "a-new-good-password"},
        headers=auth_headers,
    )
    assert response.status_code == 401


def test_change_password_rejects_short_new_password(db_session, auth_headers):
    from app.main import app

    client = TestClient(app)
    response = client.patch(
        "/api/auth/password",
        json={"current_password": "test-password-123", "new_password": "short"},
        headers=auth_headers,
    )
    assert response.status_code == 400


def test_change_password_succeeds_and_old_password_stops_working(db_session, auth_headers):
    from app.main import app

    client = TestClient(app)
    response = client.patch(
        "/api/auth/password",
        json={"current_password": "test-password-123", "new_password": "a-new-good-password"},
        headers=auth_headers,
    )
    assert response.status_code == 204

    old_login = client.post(
        "/api/auth/login", json={"email": "test-user@example.com", "password": "test-password-123"}
    )
    assert old_login.status_code == 401

    new_login = client.post(
        "/api/auth/login", json={"email": "test-user@example.com", "password": "a-new-good-password"}
    )
    assert new_login.status_code == 200


def test_change_password_sends_a_security_notice_when_mail_is_configured(db_session, auth_headers, monkeypatch):
    from app import mailer
    from app.main import app

    sent = []
    monkeypatch.setattr(mailer, "is_configured", lambda db=None: True)
    monkeypatch.setattr(
        mailer,
        "send_email",
        lambda to, subject, body, db=None: sent.append((to, subject)) or mailer.SendResult(sent=True),
    )

    client = TestClient(app)
    response = client.patch(
        "/api/auth/password",
        json={"current_password": "test-password-123", "new_password": "a-new-good-password"},
        headers=auth_headers,
    )

    assert response.status_code == 204
    assert sent == [("test-user@example.com", "Your password was changed")]


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
