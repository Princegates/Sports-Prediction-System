"""Referral program: every account gets its own code, and entering someone
else's at signup gives both sides a bonus stacked on top of whatever access
they already have -- see app.access.grant_referral_bonus.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.auth.passwords import hash_password
from app.auth.tokens import create_token
from app.config import get_settings
from app.db.models import AuditLog, User
from app.main import app

client = TestClient(app)

ADULT_DOB = "1990-01-01"


def _admin(db) -> User:
    user = User(
        email="referral-admin@example.com", name="Admin",
        password_hash=hash_password("a-good-password"), role="superadmin", status="active",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _headers(user: User) -> dict:
    settings = get_settings()
    token = create_token({"user_id": user.id, "role": user.role}, settings.secret_key, settings.session_ttl_seconds)
    return {"Authorization": f"Bearer {token}"}


def _register(email: str, referral_code: str | None = None) -> dict:
    payload = {"email": email, "name": email.split("@")[0], "password": "a-good-password", "date_of_birth": ADULT_DOB}
    if referral_code is not None:
        payload["referral_code"] = referral_code
    response = client.post("/api/auth/register", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_every_new_account_gets_its_own_referral_code(db_session):
    body = _register("has-own-code@example.com")
    assert body["user"]["referral_code"]
    assert len(body["user"]["referral_code"]) == 7


def test_registering_with_a_valid_referral_code_bonuses_both_sides(db_session):
    referrer_body = _register("referrer@example.com")
    referrer_code = referrer_body["user"]["referral_code"]

    referee_body = _register("referee@example.com", referral_code=referrer_code)
    assert "bonus" in referee_body["message"].lower()

    referrer = db_session.query(User).filter(User.email == "referrer@example.com").one()
    referee = db_session.query(User).filter(User.email == "referee@example.com").one()
    assert referee.referred_by_user_id == referrer.id

    entry = db_session.query(AuditLog).filter(AuditLog.action == "referral.bonus_granted").one()
    assert entry.target_user_id == referrer.id
    assert entry.actor_user_id == referee.id
    assert entry.detail["bonus_days"] == 3  # default referral_bonus_days


def test_referral_code_is_case_and_whitespace_insensitive(db_session):
    referrer_body = _register("mixed-case-referrer@example.com")
    referrer_code = referrer_body["user"]["referral_code"]

    referee_body = _register("mixed-case-referee@example.com", referral_code=f"  {referrer_code.lower()}  ")
    assert "bonus" in referee_body["message"].lower()


def test_an_unknown_referral_code_is_rejected(db_session):
    response = client.post(
        "/api/auth/register",
        json={
            "email": "bad-code@example.com",
            "name": "Bad Code",
            "password": "a-good-password",
            "date_of_birth": ADULT_DOB,
            "referral_code": "NOPE123",
        },
    )
    assert response.status_code == 400
    assert "referral code" in response.json()["detail"].lower()

    # Rejected before the account is created -- not left behind half-registered.
    assert db_session.query(User).filter(User.email == "bad-code@example.com").one_or_none() is None


def test_disabling_referrals_ignores_a_provided_code_without_erroring(db_session):
    admin = _admin(db_session)
    referrer_body = _register("disabled-program-referrer@example.com")
    referrer_code = referrer_body["user"]["referral_code"]

    client.patch("/api/admin/settings", json={"values": {"referral_enabled": False}}, headers=_headers(admin))

    referee_body = _register("disabled-program-referee@example.com", referral_code=referrer_code)
    assert "bonus" not in referee_body["message"].lower()
    assert db_session.query(AuditLog).filter(AuditLog.action == "referral.bonus_granted").count() == 0


def test_blank_referral_code_is_the_same_as_none(db_session):
    body = _register("blank-code@example.com", referral_code="   ")
    assert "bonus" not in body["message"].lower()
