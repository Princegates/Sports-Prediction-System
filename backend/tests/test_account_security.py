"""Email confirmation before the free trial, referral bonuses paid on the
referee's first payment, session revocation, password reset and the
self-set pause.

The audit that prompted these showed ten scripted sign-ups with one
referral code adding 30 days to the referrer's access, and an old session
token still working after a password change.
"""

from __future__ import annotations

import itertools
import re

import pytest
from fastapi.testclient import TestClient

from app import mailer
from app.auth.passwords import hash_password
from app.auth.tokens import create_token
from app.config import get_settings
from app.db.models import AccessGrant, AuditLog, User
from app.main import app

client = TestClient(app)
ADULT_DOB = "1990-01-01"
SITE = "https://socca.example"


@pytest.fixture()
def outbox(monkeypatch):
    """Mail configured with a public site URL, so signup asks for
    confirmation; every send is captured instead of delivered."""

    sent: list[tuple[str, str, str]] = []
    monkeypatch.setattr(mailer, "is_configured", lambda db=None: True)
    real_resolve = mailer.resolve_config

    def resolve(db=None):
        config = real_resolve(db)
        config.site_url = SITE
        return config

    monkeypatch.setattr(mailer, "resolve_config", resolve)
    monkeypatch.setattr(
        mailer, "send_email", lambda to, subject, body, db=None: sent.append((to, subject, body)) or mailer.SendResult(sent=True)
    )
    return sent


_signup_ips = itertools.cycle(range(1, 250))


def _register(email: str, referral_code: str | None = None) -> dict:
    payload = {"email": email, "name": "x", "password": "a-good-password", "date_of_birth": ADULT_DOB}
    if referral_code:
        payload["referral_code"] = referral_code
    # A distinct address per sign-up, so the per-IP limit (5/hour) doesn't
    # stop the tests that register many accounts.
    response = client.post("/api/auth/register", json=payload, headers={"X-Forwarded-For": f"198.18.0.{next(_signup_ips)}"})
    assert response.status_code == 200, response.text
    return response.json()


def _login(email: str, password: str = "a-good-password") -> dict:
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _link(outbox, to: str, path: str) -> str:
    body = next(b for (t, _, b) in reversed(outbox) if t == to)
    match = re.search(re.escape(SITE + path) + r"\?token=(\S+)", body)
    assert match, body
    return match.group(1)


def _active_grant(db, email: str) -> AccessGrant | None:
    db.expire_all()
    user = db.query(User).filter(User.email == email).one()
    return db.query(AccessGrant).filter(AccessGrant.user_id == user.id, AccessGrant.status == "active").one_or_none()


def _admin(db) -> dict:
    admin = User(email="sec-admin@example.com", name="A", password_hash=hash_password("admin-password"), role="superadmin", status="active")
    db.add(admin)
    db.commit()
    db.refresh(admin)
    settings = get_settings()
    return {"Authorization": f"Bearer {create_token({'user_id': admin.id}, settings.secret_key, 3600)}"}


# --- Email confirmation ------------------------------------------------------


def test_trial_waits_for_email_confirmation_when_mail_is_configured(db_session, outbox):
    body = _register("new@example.com")
    assert "confirmation link" in body["message"]
    assert "spam" in body["message"]
    assert body["user"]["email_verified"] is False
    assert _active_grant(db_session, "new@example.com") is None
    assert outbox[-1][1] == "Confirm your email"

    token = _link(outbox, "new@example.com", "/verify-email")
    response = client.post("/api/auth/verify-email", json={"token": token})
    assert response.status_code == 200, response.text
    assert response.json()["access_days"] == 5
    assert _active_grant(db_session, "new@example.com") is not None

    # A second click is harmless and doesn't stack another trial.
    again = client.post("/api/auth/verify-email", json={"token": token})
    assert again.json()["already_verified"] is True
    db_session.expire_all()
    user = db_session.query(User).filter(User.email == "new@example.com").one()
    assert db_session.query(AccessGrant).filter(AccessGrant.user_id == user.id).count() == 1


def test_trial_starts_immediately_when_confirmation_is_impossible(db_session):
    """No outbound email configured (the test default): nothing can be
    confirmed, so signup behaves exactly as it used to."""

    body = _register("no-mail@example.com")
    assert "full access" in body["message"]
    assert _active_grant(db_session, "no-mail@example.com") is not None


def test_a_bad_or_foreign_token_does_not_verify(db_session, outbox):
    _register("victim@example.com")
    assert client.post("/api/auth/verify-email", json={"token": "garbage"}).status_code == 400

    # A session token is not a confirmation link.
    session = _login("victim@example.com")["Authorization"].removeprefix("Bearer ")
    assert client.post("/api/auth/verify-email", json={"token": session}).status_code == 400


def test_a_confirmation_link_cannot_be_used_as_a_session(db_session, outbox):
    _register("link@example.com")
    token = _link(outbox, "link@example.com", "/verify-email")
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_verification_status_drives_the_banner(db_session, outbox, monkeypatch):
    _register("pending@example.com")
    headers = _login("pending@example.com")
    assert client.get("/api/auth/verification", headers=headers).json() == {"pending": True, "trial_days": 5}

    token = _link(outbox, "pending@example.com", "/verify-email")
    client.post("/api/auth/verify-email", json={"token": token})
    assert client.get("/api/auth/verification", headers=headers).json()["pending"] is False


def test_accounts_that_already_had_a_trial_are_never_pending(db_session, monkeypatch):
    _register("old-timer@example.com")  # no mail configured yet: trial starts at once
    headers = _login("old-timer@example.com")
    monkeypatch.setattr(mailer, "is_configured", lambda db=None: True)
    real_resolve = mailer.resolve_config

    def resolve(db=None):
        config = real_resolve(db)
        config.site_url = SITE
        return config

    monkeypatch.setattr(mailer, "resolve_config", resolve)
    assert client.get("/api/auth/verification", headers=headers).json()["pending"] is False


def test_resend_verification(db_session, outbox):
    _register("resend@example.com")
    headers = _login("resend@example.com")
    assert client.post("/api/auth/resend-verification", headers=headers).status_code == 204
    assert [s for (t, s, _) in outbox if t == "resend@example.com"] == ["Confirm your email", "Confirm your email"]


# --- Referral rewards --------------------------------------------------------


def test_throwaway_referrals_earn_the_referrer_nothing(db_session):
    referrer_code = _register("farmer@example.com")["user"]["referral_code"]
    before = _active_grant(db_session, "farmer@example.com").expires_at

    for i in range(10):
        _register(f"sock{i}@example.com", referral_code=referrer_code)

    assert _active_grant(db_session, "farmer@example.com").expires_at == before


def _issue_and_redeem(admin_headers: dict, email: str) -> None:
    code = client.post(
        "/api/admin/access-codes", json={"duration_days": 30, "assigned_user_email": email}, headers=admin_headers
    )
    assert code.status_code == 200, code.text
    redeemed = client.post("/api/access/redeem", json={"code": code.json()["code"]}, headers=_login(email))
    assert redeemed.status_code == 200, redeemed.text


def test_referrer_is_rewarded_once_on_the_referees_first_paid_code(db_session):
    admin = _admin(db_session)
    referrer_code = _register("ref@example.com")["user"]["referral_code"]
    _register("friend@example.com", referral_code=referrer_code)
    before = _active_grant(db_session, "ref@example.com").expires_at

    _issue_and_redeem(admin, "friend@example.com")
    after_first = _active_grant(db_session, "ref@example.com").expires_at
    assert (after_first - before).days == 3

    _issue_and_redeem(admin, "friend@example.com")
    assert _active_grant(db_session, "ref@example.com").expires_at == after_first

    stats = client.get("/api/auth/referrals", headers=_login("ref@example.com")).json()
    assert stats == {"joined": 1, "paid": 1, "bonus_days_each": 3, "enabled": True}


def test_referrer_rewards_are_capped_per_month(db_session):
    admin = _admin(db_session)
    client.patch("/api/admin/settings", json={"values": {"referral_monthly_cap": 2}}, headers=admin)
    referrer_code = _register("capped@example.com")["user"]["referral_code"]
    before = _active_grant(db_session, "capped@example.com").expires_at

    for i in range(3):
        _register(f"payer{i}@example.com", referral_code=referrer_code)
        _issue_and_redeem(admin, f"payer{i}@example.com")

    assert (_active_grant(db_session, "capped@example.com").expires_at - before).days == 6
    assert db_session.query(AuditLog).filter(AuditLog.action == "referral.bonus_capped").count() == 1


# --- Sessions ------------------------------------------------------------------


def test_sign_out_other_devices_keeps_only_the_caller(db_session):
    _register("devices@example.com")
    phone = _login("devices@example.com")
    laptop = _login("devices@example.com")

    response = client.post("/api/auth/sessions/revoke-others", headers=laptop)
    assert response.status_code == 200
    fresh = {"Authorization": f"Bearer {response.json()['access_token']}"}

    assert client.get("/api/auth/me", headers=phone).status_code == 401
    assert client.get("/api/auth/me", headers=laptop).status_code == 401
    assert client.get("/api/auth/me", headers=fresh).status_code == 200


def test_suspension_revokes_sessions_even_after_reinstatement(db_session):
    admin = _admin(db_session)
    user_id = _register("suspend-me@example.com")["user"]["id"]
    old = _login("suspend-me@example.com")

    client.post(f"/api/admin/users/{user_id}/suspend", headers=admin)
    client.post(f"/api/admin/users/{user_id}/reinstate", headers=admin)

    assert client.get("/api/auth/me", headers=old).status_code == 401
    assert client.get("/api/auth/me", headers=_login("suspend-me@example.com")).status_code == 200


# --- Password reset ------------------------------------------------------------


def test_password_reset_round_trip_is_single_use_and_signs_out_sessions(db_session, outbox):
    _register("forgot@example.com")
    old_session = _login("forgot@example.com")

    response = client.post("/api/auth/forgot-password", json={"email": "forgot@example.com"})
    assert response.status_code == 200
    token = _link(outbox, "forgot@example.com", "/reset-password")

    reset = client.post("/api/auth/reset-password", json={"token": token, "new_password": "brand-new-password"})
    assert reset.status_code == 200, reset.text

    assert client.get("/api/auth/me", headers=old_session).status_code == 401
    assert client.post("/api/auth/login", json={"email": "forgot@example.com", "password": "a-good-password"}).status_code == 401
    _login("forgot@example.com", "brand-new-password")

    replay = client.post("/api/auth/reset-password", json={"token": token, "new_password": "attacker-password"})
    assert replay.status_code == 400


def test_forgot_password_answers_the_same_for_unknown_emails(db_session, outbox):
    _register("known@example.com")
    known = client.post("/api/auth/forgot-password", json={"email": "known@example.com"})
    unknown = client.post("/api/auth/forgot-password", json={"email": "unknown@example.com"})
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()
    assert not any(t == "unknown@example.com" for (t, _, _) in outbox)


def test_forgot_password_is_rate_limited_per_email(db_session, outbox):
    _register("spam@example.com")
    codes = [client.post("/api/auth/forgot-password", json={"email": "spam@example.com"}).status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200]
    assert codes[3:] == [429, 429]


# --- Self-set pause ------------------------------------------------------------


def test_pause_blocks_predictions_and_cannot_be_shortened(db_session):
    _register("break@example.com")
    headers = _login("break@example.com")
    assert client.get("/api/predictions/today", headers=headers).status_code == 200

    week = client.post("/api/auth/pause", json={"days": 7}, headers=headers)
    assert week.status_code == 200
    until = week.json()["paused_until"]

    blocked = client.get("/api/predictions/today", headers=headers)
    assert blocked.status_code == 403
    assert "paused" in blocked.json()["detail"]
    assert client.get("/api/predictions/free-picks", headers=headers).status_code == 403

    shorter = client.post("/api/auth/pause", json={"days": 1}, headers=headers)
    assert shorter.json()["paused_until"] == until

    assert client.post("/api/auth/pause", json={"days": 3}, headers=headers).status_code == 400
    # Signing in still works, so the account can see when the break ends.
    assert client.get("/api/auth/me", headers=headers).status_code == 200


def test_input_size_limits(db_session):
    huge = "a" * 10_000
    response = client.post(
        "/api/auth/register", json={"email": "big@example.com", "name": huge, "password": "a-good-password", "date_of_birth": ADULT_DOB}
    )
    assert response.status_code == 422
    assert client.post("/api/auth/login", json={"email": "x@example.com", "password": huge}).status_code == 422
