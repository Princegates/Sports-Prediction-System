"""Superadmin settings: layering, secrets, and the things they actually change.

Configuration comes from two places -- the environment the process started
with, and overrides an operator saved since. The tests that matter are about
where those two meet, because that is where a settings panel goes wrong: a
secret that comes back out, a "reset" that stores an empty string instead of
removing the row, or a saved value that nothing reads.

The last one is the real risk. A panel that stores settings nobody consults is
worse than no panel, because it looks like it worked. So each group has a test
that the value reaches its consumer, not merely the database.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import app_settings, mailer
from app.assistant import llm
from app.auth.passwords import hash_password
from app.auth.tokens import create_token
from app.config import get_settings
from app.db.models import AppSetting, User
from app.main import app

client = TestClient(app)


def _user(db, email: str, role: str = "user") -> User:
    user = User(email=email, name=email.split("@")[0], password_hash=hash_password("a-good-password"),
                role=role, status="active")
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _headers(user: User) -> dict:
    settings = get_settings()
    token = create_token({"user_id": user.id, "role": user.role}, settings.secret_key, settings.session_ttl_seconds)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def admin(db_session):
    return _user(db_session, "settings-admin@example.com", role="superadmin")


# --- access control ----------------------------------------------------


def test_settings_are_superadmin_only(db_session, admin):
    plain = _user(db_session, "plain@example.com")
    assert client.get("/api/admin/settings", headers=_headers(plain)).status_code == 403
    assert client.patch("/api/admin/settings", json={"values": {}}, headers=_headers(plain)).status_code == 403
    assert client.get("/api/admin/system-status", headers=_headers(plain)).status_code == 403
    assert client.get("/api/admin/settings").status_code == 401


# --- secrets -----------------------------------------------------------


def test_a_saved_secret_never_comes_back(db_session, admin):
    client.patch(
        "/api/admin/settings",
        json={"values": {"smtp_password": "hunter2-app-password"}},
        headers=_headers(admin),
    )

    body = client.get("/api/admin/settings", headers=_headers(admin)).json()
    assert "hunter2" not in str(body), "the SMTP password was returned to the client"
    assert body["secrets_set"]["smtp_password"] is True
    # ...but it really was stored.
    assert app_settings.get_value(db_session, "smtp_password") == "hunter2-app-password"


def test_resubmitting_the_placeholder_leaves_the_secret_alone(db_session, admin):
    """The form shows bullets; posting them back must not overwrite the real
    password with bullets."""

    client.patch("/api/admin/settings", json={"values": {"smtp_password": "real-password"}}, headers=_headers(admin))
    shown = client.get("/api/admin/settings", headers=_headers(admin)).json()["values"]["smtp_password"]

    client.patch("/api/admin/settings", json={"values": {"smtp_password": shown}}, headers=_headers(admin))
    assert app_settings.get_value(db_session, "smtp_password") == "real-password"


# --- layering ----------------------------------------------------------


def test_unset_values_fall_back_to_the_environment(db_session, admin):
    values = client.get("/api/admin/settings", headers=_headers(admin)).json()["values"]
    assert values["ensemble_weight_elo"] == get_settings().ensemble_weight_elo
    assert client.get("/api/admin/settings", headers=_headers(admin)).json()["overridden"] == []


def test_reset_removes_the_override_rather_than_blanking_it(db_session, admin):
    client.patch("/api/admin/settings", json={"values": {"smtp_host": "smtp.example.com"}}, headers=_headers(admin))
    assert db_session.get(AppSetting, "smtp_host") is not None

    client.patch("/api/admin/settings", json={"reset": ["smtp_host"]}, headers=_headers(admin))
    assert db_session.get(AppSetting, "smtp_host") is None, "reset left a row behind"
    assert app_settings.get_value(db_session, "smtp_host") == get_settings().smtp_host


def test_an_empty_string_is_an_override_not_a_reset(db_session, admin):
    """Both are useful and they are not the same: one means 'use the
    environment', the other means 'deliberately nothing'."""

    client.patch("/api/admin/settings", json={"values": {"site_tagline": ""}}, headers=_headers(admin))
    assert db_session.get(AppSetting, "site_tagline") is not None
    assert app_settings.get_value(db_session, "site_tagline") == ""


# --- validation --------------------------------------------------------


def test_out_of_range_and_unknown_values_are_refused(db_session, admin):
    over = client.patch(
        "/api/admin/settings", json={"values": {"ensemble_weight_elo": 5}}, headers=_headers(admin)
    )
    assert over.status_code == 400
    assert "at most" in over.json()["detail"]

    bad_choice = client.patch(
        "/api/admin/settings", json={"values": {"default_theme": "neon"}}, headers=_headers(admin)
    )
    assert bad_choice.status_code == 400

    unknown = client.patch(
        "/api/admin/settings", json={"values": {"drop_database": "yes"}}, headers=_headers(admin)
    )
    assert unknown.status_code == 400


def test_nothing_is_written_when_one_field_in_a_batch_is_invalid(db_session, admin):
    response = client.patch(
        "/api/admin/settings",
        json={"values": {"site_name": "Valid Name", "elo_k_factor": 9999}},
        headers=_headers(admin),
    )
    assert response.status_code == 400
    assert db_session.get(AppSetting, "site_name") is None, "a half-applied batch left one field saved"


# --- the settings actually do something --------------------------------


def test_closing_registration_blocks_sign_ups(db_session, admin):
    open_attempt = client.post(
        "/api/auth/register",
        json={"email": "first@example.com", "name": "First", "password": "a-good-password"},
    )
    assert open_attempt.status_code == 200, open_attempt.text

    client.patch("/api/admin/settings", json={"values": {"registration_open": False}}, headers=_headers(admin))

    closed = client.post(
        "/api/auth/register",
        json={"email": "second@example.com", "name": "Second", "password": "a-good-password"},
    )
    assert closed.status_code == 403
    assert "closed" in closed.json()["detail"].lower()


def test_email_settings_reach_the_mailer(db_session, admin):
    assert mailer.is_configured(db_session) is False

    client.patch(
        "/api/admin/settings",
        json={"values": {"smtp_host": "smtp.example.com", "smtp_from": "Bot <bot@example.com>", "smtp_port": 465}},
        headers=_headers(admin),
    )

    assert mailer.is_configured(db_session) is True
    config = mailer.resolve_config(db_session)
    assert config.host == "smtp.example.com"
    assert config.port == 465


def test_resend_api_key_is_enough_to_be_configured_without_smtp_host(db_session, admin):
    """A Resend key needs no SMTP host at all -- the whole point is sending
    over HTTPS instead of an SMTP port."""

    client.patch(
        "/api/admin/settings",
        json={"values": {"resend_api_key": "re_test_key", "smtp_from": "Bot <bot@example.com>"}},
        headers=_headers(admin),
    )

    assert mailer.is_configured(db_session) is True
    config = mailer.resolve_config(db_session)
    assert config.resend_api_key == "re_test_key"
    assert config.host == ""


def test_send_email_prefers_resend_over_smtp_when_both_are_set(db_session, admin, monkeypatch):
    client.patch(
        "/api/admin/settings",
        json={
            "values": {
                "resend_api_key": "re_test_key",
                "smtp_host": "smtp.example.com",
                "smtp_from": "Bot <bot@example.com>",
            }
        },
        headers=_headers(admin),
    )

    calls: list[dict] = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append({"url": url, "headers": headers, "json": json})

        class FakeResponse:
            def raise_for_status(self):
                pass

        return FakeResponse()

    def fail_if_called(*args, **kwargs):
        raise AssertionError("SMTP path should not run when a Resend key is set")

    monkeypatch.setattr(mailer.requests, "post", fake_post)
    monkeypatch.setattr(mailer, "_connect", fail_if_called)

    result = mailer.send_email("buyer@example.com", "Your access code", "CODE-HERE", db=db_session)

    assert result.sent is True
    assert len(calls) == 1
    assert calls[0]["url"] == mailer.RESEND_API_URL
    assert calls[0]["headers"]["Authorization"] == "Bearer re_test_key"
    assert calls[0]["json"]["to"] == ["buyer@example.com"]
    assert calls[0]["json"]["from"] == "Bot <bot@example.com>"


def test_send_email_reports_a_resend_failure_without_raising(db_session, admin, monkeypatch):
    client.patch(
        "/api/admin/settings",
        json={"values": {"resend_api_key": "re_bad_key", "smtp_from": "Bot <bot@example.com>"}},
        headers=_headers(admin),
    )

    class FakeResponse:
        def raise_for_status(self):
            raise mailer.requests.HTTPError("401 Unauthorized")

    monkeypatch.setattr(mailer.requests, "post", lambda *a, **k: FakeResponse())

    result = mailer.send_email("buyer@example.com", "Your access code", "CODE-HERE", db=db_session)

    assert result.sent is False
    assert "401" in result.error


def test_branding_is_public_and_follows_the_setting(db_session, admin):
    before = client.get("/api/public/branding")
    assert before.status_code == 200
    assert before.json()["default_theme"] == "dark"
    assert before.json()["contact_whatsapp"] == "233596909643"

    client.patch(
        "/api/admin/settings",
        json={
            "values": {
                "site_name": "Prince Predicts",
                "default_theme": "light",
                "default_accent": "violet",
                "contact_whatsapp": "233209998888",
            }
        },
        headers=_headers(admin),
    )

    after = client.get("/api/public/branding").json()
    assert after["site_name"] == "Prince Predicts"
    assert after["default_theme"] == "light"
    assert after["default_accent"] == "violet"
    assert after["contact_whatsapp"] == "233209998888"


def test_model_weight_override_reaches_the_ensemble(db_session, admin):
    from app.prediction_models.ensemble import EnsembleWeights

    client.patch(
        "/api/admin/settings",
        json={"values": {"ensemble_weight_elo": 0.5, "ensemble_weight_poisson": 0.3, "ensemble_weight_ml": 0.2}},
        headers=_headers(admin),
    )

    weights = EnsembleWeights.from_settings(db_session)
    assert (weights.elo, weights.poisson, weights.ml) == (0.5, 0.3, 0.2)


# --- test email and status ---------------------------------------------


def test_assistant_llm_disabled_by_default_and_skips_the_call(db_session, admin, monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("The rewriter must not call out while disabled")

    monkeypatch.setattr(llm.requests, "post", fail_if_called)

    assert llm.is_enabled(db_session) is False
    assert llm.rewrite(db_session, "The grounded answer.", "a question") is None


def test_assistant_llm_settings_reach_the_rewriter(db_session, admin, monkeypatch):
    client.patch(
        "/api/admin/settings",
        json={
            "values": {
                "assistant_llm_enabled": True,
                "assistant_llm_base_url": "https://example-llm.test/v1",
                "assistant_llm_model": "test-model",
                "assistant_llm_api_key": "sk-test-key",
            }
        },
        headers=_headers(admin),
    )

    calls: list[dict] = []

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "A friendlier version."}}]}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return FakeResponse()

    monkeypatch.setattr(llm.requests, "post", fake_post)

    assert llm.is_enabled(db_session) is True
    result = llm.rewrite(db_session, "The grounded answer.", "the user's question")

    assert result == "A friendlier version."
    assert len(calls) == 1
    assert calls[0]["url"] == "https://example-llm.test/v1/chat/completions"
    assert calls[0]["headers"]["Authorization"] == "Bearer sk-test-key"
    assert calls[0]["json"]["model"] == "test-model"


def test_assistant_llm_a_failed_call_falls_back_to_none_not_an_exception(db_session, admin, monkeypatch):
    client.patch(
        "/api/admin/settings",
        json={
            "values": {
                "assistant_llm_enabled": True,
                "assistant_llm_base_url": "https://example-llm.test/v1",
            }
        },
        headers=_headers(admin),
    )

    class FakeResponse:
        def raise_for_status(self):
            raise llm.requests.HTTPError("500 Server Error")

    monkeypatch.setattr(llm.requests, "post", lambda *a, **k: FakeResponse())

    assert llm.rewrite(db_session, "The grounded answer.", "a question") is None


def test_assistant_llm_failure_logs_the_providers_own_error_body(db_session, admin, monkeypatch, caplog):
    """The status line alone ("400 Bad Request") doesn't say *why* -- the
    provider's JSON error body does, and that's what an operator actually
    needs to fix a misconfiguration. This pins down that it reaches the log,
    not just the status line."""

    client.patch(
        "/api/admin/settings",
        json={
            "values": {
                "assistant_llm_enabled": True,
                "assistant_llm_base_url": "https://example-llm.test/v1",
            }
        },
        headers=_headers(admin),
    )

    class FakeResponse:
        text = '{"error": {"message": "Invalid value at messages[0]"}}'

        def raise_for_status(self):
            raise llm.requests.HTTPError("400 Client Error: Bad Request", response=self)

    monkeypatch.setattr(llm.requests, "post", lambda *a, **k: FakeResponse())

    with caplog.at_level("WARNING", logger=llm.logger.name):
        assert llm.rewrite(db_session, "The grounded answer.", "a question") is None

    assert "Invalid value at messages[0]" in caplog.text


def test_assistant_llm_api_key_is_masked_like_other_secrets(db_session, admin):
    client.patch(
        "/api/admin/settings",
        json={"values": {"assistant_llm_api_key": "sk-super-secret"}},
        headers=_headers(admin),
    )
    body = client.get("/api/admin/settings", headers=_headers(admin)).json()
    assert "sk-super-secret" not in str(body)
    assert body["secrets_set"]["assistant_llm_api_key"] is True


def test_test_email_says_why_it_cannot_send(db_session, admin):
    response = client.post("/api/admin/settings/test-email", json={}, headers=_headers(admin))
    assert response.status_code == 200
    body = response.json()
    assert body["sent"] is False
    assert "not configured" in body["detail"].lower()


def test_test_email_defaults_to_the_admins_own_address(db_session, admin, monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(mailer, "is_configured", lambda *a, **k: True)
    monkeypatch.setattr(
        mailer, "send_email",
        lambda to, subject, body, **kw: (sent.append(to), mailer.SendResult(sent=True))[1],
    )

    response = client.post("/api/admin/settings/test-email", json={}, headers=_headers(admin))
    assert response.json()["sent"] is True
    assert sent == [admin.email]


def test_system_status_reports_the_layers(db_session, admin):
    body = client.get("/api/admin/system-status", headers=_headers(admin)).json()
    assert body["database_reachable"] is True
    assert body["email_configured"] is False
    for key in ("matches", "predictions", "users", "active_grants", "unredeemed_codes"):
        assert isinstance(body[key], int)
