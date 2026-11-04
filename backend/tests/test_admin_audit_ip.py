"""Every audit-log entry records the address of the request that caused it,
without each of the ~30 call sites having to pass it in -- see
app.request_context. Entries the server writes on its own have none."""

from __future__ import annotations

from app.db.models import AuditLog
from app.request_context import set_client_ip


def test_an_entry_written_during_a_request_carries_its_ip(db_session):
    set_client_ip("203.0.113.7")
    try:
        db_session.add(AuditLog(action="test.event"))
        db_session.commit()
    finally:
        set_client_ip(None)

    assert db_session.query(AuditLog).one().ip == "203.0.113.7"


def test_an_entry_written_outside_a_request_has_no_ip(db_session):
    db_session.add(AuditLog(action="test.event"))
    db_session.commit()

    assert db_session.query(AuditLog).one().ip is None


def test_the_ip_reaches_the_activity_log_endpoint_end_to_end(db_session):
    from fastapi.testclient import TestClient

    from app.api import rate_limit
    from app.auth.passwords import hash_password
    from app.auth.tokens import create_token
    from app.config import get_settings
    from app.db.models import User
    from app.main import app

    client = TestClient(app)
    rate_limit.reset()
    header = get_settings().client_ip_header or "x-forwarded-for"

    registered = client.post(
        "/api/auth/register",
        json={"email": "ip-user@example.com", "name": "Ip User", "password": "a-good-password", "date_of_birth": "1990-01-01"},
        headers={header: "198.51.100.23"},
    )
    assert registered.status_code == 200

    admin = User(email="ip-admin@example.com", name="Admin", password_hash=hash_password("a-good-password"),
                 role="superadmin", status="active")
    db_session.add(admin)
    db_session.commit()
    settings = get_settings()
    token = create_token({"user_id": admin.id, "role": admin.role}, settings.secret_key, settings.session_ttl_seconds)

    items = client.get("/api/admin/audit-log?action=account.registered", headers={"Authorization": f"Bearer {token}"}).json()["items"]
    assert [row["ip"] for row in items] == ["198.51.100.23"]
