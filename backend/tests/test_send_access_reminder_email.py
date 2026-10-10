"""scripts/send_access_reminder_email.py -- the "you're still on the free
tier" nudge to active accounts with no live access grant.

Two independent gates (the admin-level setting, email configuration) and
the recipient query itself (active, no live grant, not a superadmin) --
each tested on its own, same split as test_send_weekly_picks_email.py.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from pathlib import Path

import pytest

from app import app_settings, mailer
from app.access import create_access_code, redeem_access_code
from app.db.models import User


@pytest.fixture()
def script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "send_access_reminder_email.py"
    spec = importlib.util.spec_from_file_location("send_access_reminder_email_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _enable(db_session) -> None:
    app_settings.set_values(db_session, {"access_reminder_email_enabled": True})


def _configured_mailer(monkeypatch):
    sent: list[tuple] = []
    monkeypatch.setattr(mailer, "is_configured", lambda *a, **k: True)
    monkeypatch.setattr(
        mailer, "send_email",
        lambda to, subject, body, *a, **kw: (sent.append((to, subject, body)), mailer.SendResult(sent=True))[1],
    )
    return sent


def _user(db_session, email: str, *, status: str = "active", role: str = "user") -> User:
    from app.auth.passwords import hash_password

    user = User(email=email, name="Test", password_hash=hash_password("a-good-password"), role=role, status=status)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def test_does_nothing_when_the_admin_setting_is_off(db_session, script, monkeypatch, capsys):
    _configured_mailer(monkeypatch)
    _user(db_session, "member@example.com")

    sys.argv = ["send_access_reminder_email.py"]
    script.main()

    assert "access_reminder_email_enabled is off" in capsys.readouterr().out


def test_does_nothing_when_email_is_not_configured(db_session, script, monkeypatch, capsys):
    _enable(db_session)
    monkeypatch.setattr(mailer, "is_configured", lambda *a, **k: False)
    _user(db_session, "member@example.com")

    sys.argv = ["send_access_reminder_email.py"]
    script.main()

    assert "isn't configured" in capsys.readouterr().out


def test_sends_only_to_active_accounts_with_no_live_grant(db_session, script, monkeypatch, capsys):
    _enable(db_session)
    sent = _configured_mailer(monkeypatch)

    no_access = _user(db_session, "no-access@example.com")
    has_access = _user(db_session, "has-access@example.com")
    code = create_access_code(db_session, _user(db_session, "admin@example.com", role="superadmin"),
                               duration_days=30, redemption_limit=1, assigned_user_id=has_access.id,
                               assigned_email=has_access.email)
    redeem_access_code(db_session, has_access, code.code)
    _user(db_session, "suspended@example.com", status="suspended")
    _user(db_session, "the-admin@example.com", role="superadmin")

    sys.argv = ["send_access_reminder_email.py"]
    script.main()

    out = capsys.readouterr().out
    assert "Recipients: 1 active account(s)" in out
    assert "Sent 1, failed 0, of 1." in out

    assert len(sent) == 1
    to, subject, body = sent[0]
    assert to == no_access.email
    assert subject == "Unlock full AI Picks"


def test_an_expired_grant_still_counts_as_no_access(db_session, script, monkeypatch):
    _enable(db_session)
    sent = _configured_mailer(monkeypatch)

    lapsed = _user(db_session, "lapsed@example.com")
    admin = _user(db_session, "admin2@example.com", role="superadmin")
    code = create_access_code(db_session, admin, duration_days=1, redemption_limit=1,
                               assigned_user_id=lapsed.id, assigned_email=lapsed.email)
    grant = redeem_access_code(db_session, lapsed, code.code)
    grant.expires_at = dt.datetime.utcnow() - dt.timedelta(days=1)
    db_session.commit()

    sys.argv = ["send_access_reminder_email.py"]
    script.main()

    assert [to for to, _s, _b in sent] == [lapsed.email]


def test_dry_run_sends_nothing_but_lists_recipients(db_session, script, monkeypatch, capsys):
    _enable(db_session)
    sent = _configured_mailer(monkeypatch)
    _user(db_session, "member@example.com")

    sys.argv = ["send_access_reminder_email.py", "--dry-run"]
    script.main()

    out = capsys.readouterr().out
    assert "Dry run -- nothing sent" in out
    assert "member@example.com" in out
    assert sent == []


def test_nobody_to_remind_is_reported_and_nothing_sent(db_session, script, monkeypatch, capsys):
    _enable(db_session)
    sent = _configured_mailer(monkeypatch)

    sys.argv = ["send_access_reminder_email.py"]
    script.main()

    assert "Nobody to remind." in capsys.readouterr().out
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

    sys.argv = ["send_access_reminder_email.py"]
    script.main()  # must not raise -- a mix of successes and failures is not a fatal run

    assert "Sent 1, failed 1, of 2." in capsys.readouterr().out


def test_every_send_failing_exits_nonzero(db_session, script, monkeypatch):
    _enable(db_session)
    monkeypatch.setattr(mailer, "is_configured", lambda *a, **k: True)
    monkeypatch.setattr(mailer, "send_email", lambda *a, **k: mailer.SendResult(sent=False, error="Connection refused"))
    _user(db_session, "member@example.com")

    sys.argv = ["send_access_reminder_email.py"]
    with pytest.raises(SystemExit) as exc:
        script.main()
    assert exc.value.code == 1


def test_access_reminder_message_mentions_the_free_tier_and_links_back():
    subject, body = mailer.access_reminder_message("https://soccaintel.com", whatsapp="233596909643")
    assert subject == "Unlock full AI Picks"
    assert "free tier" in body
    assert "https://soccaintel.com" in body
    assert "+233 59 690 9643" in body
    assert "WhatsApp only" in body


def test_access_reminder_message_omits_contact_line_when_not_configured():
    _subject, body = mailer.access_reminder_message(whatsapp=None)
    assert "WhatsApp" not in body
