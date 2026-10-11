"""scripts/send_pricing_announcement_email.py -- the one-time "access is
GHC 20 / week" broadcast to every active account, regardless of whether
they already have a live access grant.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from app import app_settings, mailer
from app.db.models import User


@pytest.fixture()
def script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "send_pricing_announcement_email.py"
    spec = importlib.util.spec_from_file_location("send_pricing_announcement_email_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _enable(db_session, *, price_label: str = "GHC 20 / week") -> None:
    app_settings.set_values(db_session, {"pricing_announcement_email_enabled": True, "pricing_label": price_label})


def _configured_mailer(monkeypatch):
    sent: list[tuple] = []
    monkeypatch.setattr(mailer, "is_configured", lambda *a, **k: True)
    monkeypatch.setattr(
        mailer, "send_email",
        lambda to, subject, body, *a, **kw: (sent.append((to, subject, body)), mailer.SendResult(sent=True))[1],
    )
    return sent


def _user(db_session, email: str, *, status: str = "active") -> User:
    from app.auth.passwords import hash_password

    user = User(email=email, name="Test", password_hash=hash_password("a-good-password"), role="user", status=status)
    db_session.add(user)
    db_session.commit()
    return user


def test_does_nothing_when_the_admin_setting_is_off(db_session, script, monkeypatch, capsys):
    _configured_mailer(monkeypatch)
    _user(db_session, "member@example.com")

    sys.argv = ["send_pricing_announcement_email.py"]
    script.main()

    assert "pricing_announcement_email_enabled is off" in capsys.readouterr().out


def test_does_nothing_when_email_is_not_configured(db_session, script, monkeypatch, capsys):
    _enable(db_session)
    monkeypatch.setattr(mailer, "is_configured", lambda *a, **k: False)
    _user(db_session, "member@example.com")

    sys.argv = ["send_pricing_announcement_email.py"]
    script.main()

    assert "isn't configured" in capsys.readouterr().out


def test_does_nothing_when_no_price_is_set(db_session, script, monkeypatch, capsys):
    app_settings.set_values(db_session, {"pricing_announcement_email_enabled": True, "pricing_label": ""})
    sent = _configured_mailer(monkeypatch)
    _user(db_session, "member@example.com")

    sys.argv = ["send_pricing_announcement_email.py"]
    script.main()

    assert "pricing_label is blank" in capsys.readouterr().out
    assert sent == []


def test_sends_to_every_active_account_regardless_of_access(db_session, script, monkeypatch, capsys):
    _enable(db_session, price_label="GHC 20 / week")
    sent = _configured_mailer(monkeypatch)

    _user(db_session, "no-access@example.com")
    _user(db_session, "has-access@example.com")
    _user(db_session, "suspended@example.com", status="suspended")

    sys.argv = ["send_pricing_announcement_email.py"]
    script.main()

    out = capsys.readouterr().out
    assert "Announcing: 'GHC 20 / week'" in out
    assert "Recipients: 2 active account(s)." in out
    assert "Sent 2, failed 0, of 2." in out

    recipients = {to for to, _s, _b in sent}
    assert recipients == {"no-access@example.com", "has-access@example.com"}
    for _to, subject, body in sent:
        assert subject == "Access is GHC 20 / week"
        assert "GHC 20 / week" in body


def test_dry_run_sends_nothing_but_shows_the_message_and_recipients(db_session, script, monkeypatch, capsys):
    _enable(db_session, price_label="GHC 20 / week")
    sent = _configured_mailer(monkeypatch)
    _user(db_session, "member@example.com")

    sys.argv = ["send_pricing_announcement_email.py", "--dry-run"]
    script.main()

    out = capsys.readouterr().out
    assert "GHC 20 / week" in out
    assert "member@example.com" in out
    assert "Dry run -- nothing sent" in out
    assert sent == []


def test_nobody_to_email_is_reported_and_nothing_sent(db_session, script, monkeypatch, capsys):
    _enable(db_session)
    sent = _configured_mailer(monkeypatch)

    sys.argv = ["send_pricing_announcement_email.py"]
    script.main()

    assert "Nobody to email." in capsys.readouterr().out
    assert sent == []


def test_a_partial_failure_is_reported_but_not_fatal(db_session, script, monkeypatch, capsys):
    _enable(db_session)
    monkeypatch.setattr(mailer, "is_configured", lambda *a, **k: True)

    def fake_send(to, subject, body, *a, **kwargs):
        if to == "bounces@example.com":
            return mailer.SendResult(sent=False, error="SMTPRecipientsRefused")
        return mailer.SendResult(sent=True)

    monkeypatch.setattr(mailer, "send_email", fake_send)
    _user(db_session, "ok@example.com")
    _user(db_session, "bounces@example.com")

    sys.argv = ["send_pricing_announcement_email.py"]
    script.main()  # must not raise -- a mix of successes and failures is not a fatal run

    assert "Sent 1, failed 1, of 2." in capsys.readouterr().out


def test_every_send_failing_exits_nonzero(db_session, script, monkeypatch):
    _enable(db_session)
    monkeypatch.setattr(mailer, "is_configured", lambda *a, **k: True)
    monkeypatch.setattr(mailer, "send_email", lambda *a, **k: mailer.SendResult(sent=False, error="Connection refused"))
    _user(db_session, "member@example.com")

    sys.argv = ["send_pricing_announcement_email.py"]
    with pytest.raises(SystemExit) as exc:
        script.main()
    assert exc.value.code == 1


def test_pricing_announcement_message_names_the_price_and_links_back():
    subject, body = mailer.pricing_announcement_message("GHC 20 / week", "https://soccaintel.com", whatsapp="233596909643")
    assert subject == "Access is GHC 20 / week"
    assert "GHC 20 / week" in body
    assert "https://soccaintel.com" in body
    assert "+233 59 690 9643" in body
    assert "WhatsApp only" in body


def test_pricing_announcement_message_omits_contact_line_when_not_configured():
    _subject, body = mailer.pricing_announcement_message("GHC 20 / week", whatsapp=None)
    assert "WhatsApp" not in body
