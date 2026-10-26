"""Hubtel payments: Hubtel itself is faked throughout. What's under test is
our own rules -- above all that nothing grants access on a callback's say-so,
only on Hubtel's own confirmation, and never twice.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import app_settings
from app.db.models import AccessCode, AuditLog, Payment, User
from app.main import app
from app.payments import hubtel

client = TestClient(app)


@pytest.fixture()
def configured(db_session):
    app_settings.set_values(
        db_session,
        {
            "hubtel_enabled": True,
            "hubtel_client_id": "id",
            "hubtel_client_secret": "secret",
            "hubtel_merchant_account": "11684",
            "public_api_url": "https://api.example.test",
            "public_site_url": "https://example.test",
            "price_amount": 20,
            "price_days": 5,
        },
    )


@pytest.fixture()
def fake_hubtel(monkeypatch):
    """Records what we asked Hubtel; ``state['status']`` is what it answers."""

    state = {"status": "pending", "amount": 20.0, "created": [], "checks": [], "fail_status": False}

    def create_checkout(config, **kwargs):
        state["created"].append(kwargs)
        return hubtel.CheckoutResult(checkout_id="CHK1", checkout_url="https://pay.hubtel.test/abc")

    def check_status(config, reference):
        state["checks"].append(reference)
        if state["fail_status"]:
            raise hubtel.HubtelError("unreachable")
        return hubtel.StatusResult(state=state["status"], amount=state["amount"], raw={"status": state["status"]})

    monkeypatch.setattr(hubtel, "create_checkout", create_checkout)
    monkeypatch.setattr(hubtel, "check_status", check_status)
    return state


def _user_id(db_session) -> int:
    return db_session.query(User).filter(User.email == "test-user@example.com").one().id


def _hubtel_codes(db_session) -> int:
    # The auth_headers fixture's own grant is also an "issued" code, so count
    # only the ones this feature cut.
    return db_session.query(AccessCode).filter(AccessCode.notes.like("Hubtel payment%")).count()


def _checkout(auth_headers):
    response = client.post("/api/payments/checkout", headers=auth_headers)
    assert response.status_code == 200, response.text
    return response.json()


def _callback(reference: str):
    return client.post("/api/payments/hubtel/callback", json={"Data": {"ClientReference": reference}})


# --- options / checkout --------------------------------------------------------


def test_options_are_disabled_until_hubtel_is_configured(auth_headers):
    body = client.get("/api/payments/options", headers=auth_headers).json()
    assert body["enabled"] is False


def test_options_enabled_once_configured_with_a_price(auth_headers, configured):
    body = client.get("/api/payments/options", headers=auth_headers).json()
    assert body == {"enabled": True, "amount": 20.0, "days": 5, "currency": "GH₵"}


def test_options_need_a_price_even_when_hubtel_is_on(db_session, auth_headers, configured):
    app_settings.set_values(db_session, {"price_amount": 0})
    assert client.get("/api/payments/options", headers=auth_headers).json()["enabled"] is False


def test_checkout_requires_login():
    assert client.post("/api/payments/checkout").status_code == 401


def test_checkout_is_refused_when_not_enabled(auth_headers, fake_hubtel):
    response = client.post("/api/payments/checkout", headers=auth_headers)
    assert response.status_code == 400
    assert fake_hubtel["created"] == []


def test_checkout_creates_a_pending_payment_and_returns_the_hubtel_page(
    db_session, auth_headers, configured, fake_hubtel
):
    body = _checkout(auth_headers)
    assert body["checkout_url"] == "https://pay.hubtel.test/abc"
    assert len(body["reference"]) == 32

    payment = db_session.query(Payment).one()
    assert payment.client_reference == body["reference"]
    assert (payment.status, payment.amount, payment.access_days) == ("pending", 20.0, 5)
    assert payment.user_id == _user_id(db_session)

    sent = fake_hubtel["created"][0]
    assert sent["amount"] == 20.0
    assert sent["callback_url"] == "https://api.example.test/api/payments/hubtel/callback"
    assert sent["return_url"].startswith("https://example.test/app/access?payment=")


def test_checkout_failure_marks_the_payment_failed(db_session, auth_headers, configured, monkeypatch):
    def boom(config, **kwargs):
        raise hubtel.HubtelError("Couldn't reach Hubtel to start the payment.")

    monkeypatch.setattr(hubtel, "create_checkout", boom)
    response = client.post("/api/payments/checkout", headers=auth_headers)
    assert response.status_code == 400
    assert db_session.query(Payment).one().status == "failed"


# --- the callback never grants on its own say-so -----------------------------


def test_a_callback_claiming_success_grants_nothing_unless_hubtel_agrees(
    db_session, auth_headers, configured, fake_hubtel
):
    reference = _checkout(auth_headers)["reference"]
    fake_hubtel["status"] = "pending"  # Hubtel says: not paid

    forged = client.post(
        "/api/payments/hubtel/callback",
        json={"ResponseCode": "0000", "Status": "Success", "Data": {"ClientReference": reference, "Status": "Success"}},
    )
    assert forged.status_code == 200
    db_session.expire_all()
    assert db_session.query(Payment).one().status == "pending"
    assert _hubtel_codes(db_session) == 0


def test_a_paid_callback_grants_access_through_an_ordinary_code(db_session, auth_headers, configured, fake_hubtel):
    reference = _checkout(auth_headers)["reference"]
    fake_hubtel["status"] = "paid"

    assert _callback(reference).json() == {"ok": True}

    db_session.expire_all()
    payment = db_session.query(Payment).one()
    assert payment.status == "completed" and payment.completed_at is not None
    code = db_session.get(AccessCode, payment.access_code_id)
    assert code.kind == "issued" and code.duration_days == 5
    assert reference in code.notes
    assert db_session.query(AuditLog).filter(AuditLog.action == "payment.completed").count() == 1


def test_a_repeated_callback_does_not_grant_twice(db_session, auth_headers, configured, fake_hubtel):
    reference = _checkout(auth_headers)["reference"]
    fake_hubtel["status"] = "paid"

    _callback(reference)
    _callback(reference)
    _callback(reference)

    db_session.expire_all()
    assert _hubtel_codes(db_session) == 1
    assert db_session.query(AuditLog).filter(AuditLog.action == "payment.completed").count() == 1


def test_a_short_payment_is_not_granted(db_session, auth_headers, configured, fake_hubtel):
    reference = _checkout(auth_headers)["reference"]
    fake_hubtel.update(status="paid", amount=5.0)

    _callback(reference)

    db_session.expire_all()
    assert db_session.query(Payment).one().status == "failed"
    assert _hubtel_codes(db_session) == 0
    assert db_session.query(AuditLog).filter(AuditLog.action == "payment.amount_mismatch").count() == 1


def test_hubtel_saying_unpaid_cancels_the_payment(db_session, auth_headers, configured, fake_hubtel):
    reference = _checkout(auth_headers)["reference"]
    fake_hubtel["status"] = "unpaid"
    _callback(reference)
    db_session.expire_all()
    assert db_session.query(Payment).one().status == "cancelled"


def test_an_unreachable_hubtel_means_503_and_no_change(db_session, auth_headers, configured, fake_hubtel):
    reference = _checkout(auth_headers)["reference"]
    fake_hubtel["fail_status"] = True

    assert _callback(reference).status_code == 503

    db_session.expire_all()
    assert db_session.query(Payment).one().status == "pending"


def test_a_callback_for_an_unknown_reference_is_a_quiet_200(db_session, fake_hubtel):
    response = _callback("SI" + "0" * 30)
    assert response.status_code == 200 and response.json() == {"ok": False}
    assert fake_hubtel["checks"] == []


def test_a_malformed_callback_never_reaches_hubtel(db_session, fake_hubtel):
    response = client.post("/api/payments/hubtel/callback", json={"nonsense": True})
    assert response.status_code == 200 and response.json() == {"ok": False}
    assert fake_hubtel["checks"] == []


# --- polling from the page the member returns to ------------------------------


def test_status_poll_confirms_a_payment_whose_callback_never_arrived(
    db_session, auth_headers, configured, fake_hubtel
):
    reference = _checkout(auth_headers)["reference"]
    fake_hubtel["status"] = "paid"

    body = client.get(f"/api/payments/{reference}/status", headers=auth_headers).json()

    assert body["status"] == "completed"
    assert body["access_expires_at"] is not None


def test_status_poll_does_not_hammer_hubtel(db_session, auth_headers, configured, fake_hubtel):
    reference = _checkout(auth_headers)["reference"]

    for _ in range(5):
        assert client.get(f"/api/payments/{reference}/status", headers=auth_headers).json()["status"] == "pending"

    assert len(fake_hubtel["checks"]) == 1


def test_status_poll_survives_hubtel_being_unreachable(db_session, auth_headers, configured, fake_hubtel):
    reference = _checkout(auth_headers)["reference"]
    fake_hubtel["fail_status"] = True
    response = client.get(f"/api/payments/{reference}/status", headers=auth_headers)
    assert response.status_code == 200 and response.json()["status"] == "pending"


def test_status_is_private_to_the_payer(db_session, auth_headers, configured, fake_hubtel):
    from app.auth.passwords import hash_password
    from app.auth.tokens import create_token
    from app.config import get_settings

    reference = _checkout(auth_headers)["reference"]
    other = User(
        email="other@example.com", name="Other", password_hash=hash_password("a-good-password"),
        role="user", status="active",
    )
    db_session.add(other)
    db_session.commit()
    db_session.refresh(other)
    settings = get_settings()
    token = create_token({"user_id": other.id, "role": other.role}, settings.secret_key, settings.session_ttl_seconds)

    response = client.get(f"/api/payments/{reference}/status", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 404


# --- admin list, hubtel response parsing, account deletion ---------------------


def test_admin_can_list_payments_and_members_cannot(db_session, auth_headers, configured, fake_hubtel):
    from app.auth.passwords import hash_password
    from app.auth.tokens import create_token
    from app.config import get_settings

    _checkout(auth_headers)
    assert client.get("/api/admin/payments", headers=auth_headers).status_code == 403

    admin = User(
        email="admin@example.com", name="Admin", password_hash=hash_password("a-good-password"),
        role="superadmin", status="active",
    )
    db_session.add(admin)
    db_session.commit()
    db_session.refresh(admin)
    settings = get_settings()
    token = create_token({"user_id": admin.id, "role": admin.role}, settings.secret_key, settings.session_ttl_seconds)
    rows = client.get("/api/admin/payments", headers={"Authorization": f"Bearer {token}"}).json()
    assert len(rows) == 1 and rows[0]["user_email"] == "test-user@example.com" and rows[0]["status"] == "pending"


def test_check_status_parses_paid_and_unpaid_and_unknown(monkeypatch):
    class Resp:
        def __init__(self, data):
            self._data = data

        def raise_for_status(self):
            pass

        def json(self):
            return {"data": self._data}

    config = hubtel.HubtelConfig("id", "secret", "11684", "https://x.test/init", "https://s.test", "https://api.test")
    for status, amount, expected in [("Paid", 20, "paid"), ("Unpaid", None, "unpaid"), ("Pending", 20, "pending"), ("???", 1, "pending")]:
        monkeypatch.setattr(hubtel.requests, "get", lambda *a, _d={"status": status, "amount": amount}, **k: Resp(_d))
        assert hubtel.check_status(config, "SIREF").state == expected


def test_deleting_an_account_keeps_its_payment_record(db_session, auth_headers, configured, fake_hubtel):
    from app.account_deletion import delete_user_account
    from app.auth.passwords import hash_password

    _checkout(auth_headers)
    admin = User(
        email="admin2@example.com", name="Admin", password_hash=hash_password("a-good-password"),
        role="superadmin", status="active",
    )
    db_session.add(admin)
    db_session.commit()
    member = db_session.query(User).filter(User.email == "test-user@example.com").one()

    delete_user_account(db_session, admin, member)

    payment = db_session.query(Payment).one()
    assert payment.user_id is None
