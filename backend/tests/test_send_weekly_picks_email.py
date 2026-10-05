"""scripts/send_weekly_picks_email.py -- the weekly "picks are up" nudge.

Two independent gates (the admin-level setting, email configuration) and
two independent filters on who gets it (account status, the per-user
opt-out) -- each tested on its own so a future change to one can't silently
break another.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from pathlib import Path

import pytest

from app import app_settings, mailer
from app.db.models import AdminPick, User


@pytest.fixture()
def script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "send_weekly_picks_email.py"
    spec = importlib.util.spec_from_file_location("send_weekly_picks_email_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _enable(db_session) -> None:
    app_settings.set_values(db_session, {"weekly_picks_email_enabled": True})


def _configured_mailer(monkeypatch):
    sent: list[tuple] = []
    monkeypatch.setattr(mailer, "is_configured", lambda *a, **k: True)
    monkeypatch.setattr(
        mailer, "send_email",
        lambda to, subject, body, *a, **kw: (sent.append((to, subject, body)), mailer.SendResult(sent=True))[1],
    )
    return sent


def _weekly_pick(db_session, source: str, label: str, result: str = "pending") -> AdminPick:
    pick = AdminPick(
        source=source, label=label, legs=[], priced=True, result=result,
        expires_at=dt.datetime.utcnow() + dt.timedelta(days=5),
    )
    db_session.add(pick)
    db_session.commit()
    return pick


def _user(db_session, email: str, *, status: str = "active", notify: bool = True) -> User:
    from app.auth.passwords import hash_password

    user = User(email=email, name="Test", password_hash=hash_password("a-good-password"),
                role="user", status=status, notify_weekly_picks=notify)
    db_session.add(user)
    db_session.commit()
    return user


def test_does_nothing_when_the_admin_setting_is_off(db_session, script, monkeypatch, capsys):
    _configured_mailer(monkeypatch)
    _weekly_pick(db_session, "system_weekly_low", "Low Risk -- Weekly 10-Leg Accumulator (5-10 odds)")
    _user(db_session, "member@example.com")

    sys.argv = ["send_weekly_picks_email.py"]
    script.main()

    assert "weekly_picks_email_enabled is off" in capsys.readouterr().out


def test_does_nothing_when_email_is_not_configured(db_session, script, monkeypatch, capsys):
    _enable(db_session)
    monkeypatch.setattr(mailer, "is_configured", lambda *a, **k: False)
    _weekly_pick(db_session, "system_weekly_low", "Low Risk -- Weekly 10-Leg Accumulator (5-10 odds)")
    _user(db_session, "member@example.com")

    sys.argv = ["send_weekly_picks_email.py"]
    script.main()

    assert "isn't configured" in capsys.readouterr().out


def test_does_nothing_when_no_weekly_picks_are_live(db_session, script, monkeypatch, capsys):
    _enable(db_session)
    sent = _configured_mailer(monkeypatch)
    _user(db_session, "member@example.com")
    # A settled pick (not pending) and a random daily pick don't count --
    # neither is "this week's" live announcement.
    _weekly_pick(db_session, "system_weekly_low", "stale", result="won")
    _weekly_pick(db_session, "system_random_daily_1", "not a weekly tier")

    sys.argv = ["send_weekly_picks_email.py"]
    script.main()

    assert "nothing to announce" in capsys.readouterr().out
    assert sent == []


def test_sends_to_opted_in_active_accounts_only(db_session, script, monkeypatch, capsys):
    _enable(db_session)
    sent = _configured_mailer(monkeypatch)
    _weekly_pick(db_session, "system_weekly_low", "Low Risk -- Weekly 10-Leg Accumulator (5-10 odds)")
    _weekly_pick(db_session, "system_weekly_jackpot_1", "High Risk Jackpot #1 -- Weekly 17-Leg Accumulator (67+ odds)")
    _user(db_session, "opted-in@example.com", notify=True)
    _user(db_session, "opted-out@example.com", notify=False)
    _user(db_session, "suspended@example.com", status="suspended", notify=True)

    sys.argv = ["send_weekly_picks_email.py"]
    script.main()

    out = capsys.readouterr().out
    assert "Recipients: 1 active account(s)" in out
    assert "Sent 1, failed 0, of 1." in out

    assert len(sent) == 1
    to, subject, body = sent[0]
    assert to == "opted-in@example.com"
    assert subject == "This week's picks are up"
    assert "Low Risk -- Weekly 10-Leg Accumulator (5-10 odds)" in body
    assert "High Risk Jackpot #1 -- Weekly 17-Leg Accumulator (67+ odds)" in body


def test_dry_run_sends_nothing(db_session, script, monkeypatch, capsys):
    _enable(db_session)
    sent = _configured_mailer(monkeypatch)
    _weekly_pick(db_session, "system_weekly_low", "Low Risk -- Weekly 10-Leg Accumulator (5-10 odds)")
    _user(db_session, "member@example.com")

    sys.argv = ["send_weekly_picks_email.py", "--dry-run"]
    script.main()

    assert "Dry run -- nothing sent." in capsys.readouterr().out
    assert sent == []


def test_a_partial_failure_is_reported_but_not_fatal(db_session, script, monkeypatch, capsys):
    _enable(db_session)
    monkeypatch.setattr(mailer, "is_configured", lambda *a, **k: True)

    def fake_send(to, subject, body, *a, **kwargs):
        if to == "bounces@example.com":
            return mailer.SendResult(sent=False, error="SMTPRecipientsRefused")
        return mailer.SendResult(sent=True)

    monkeypatch.setattr(mailer, "send_email", fake_send)
    _weekly_pick(db_session, "system_weekly_low", "Low Risk -- Weekly 10-Leg Accumulator (5-10 odds)")
    _user(db_session, "ok@example.com")
    _user(db_session, "bounces@example.com")

    sys.argv = ["send_weekly_picks_email.py"]
    script.main()  # must not raise -- a mix of successes and failures is not a fatal run

    assert "Sent 1, failed 1, of 2." in capsys.readouterr().out


def test_weekly_picks_message_lists_each_label_and_links_back():
    subject, body = mailer.weekly_picks_message(
        ["Low Risk -- Weekly 10-Leg Accumulator (5-10 odds)", "Medium Risk -- Weekly 10-Leg Accumulator (11-20 odds)"],
        site_url="https://soccaintel.com",
    )
    assert subject == "This week's picks are up"
    assert "Low Risk -- Weekly 10-Leg Accumulator (5-10 odds)" in body
    assert "Medium Risk -- Weekly 10-Leg Accumulator (11-20 odds)" in body
    assert "https://soccaintel.com" in body
    assert "Profile -> Email preferences" in body


def test_weekly_picks_message_with_no_labels_says_so_rather_than_an_empty_list():
    _, body = mailer.weekly_picks_message([], site_url=None)
    assert "nothing cleared this week's targets" in body


def test_every_send_failing_exits_nonzero(db_session, script, monkeypatch):
    """A total wipeout almost certainly means a bad SMTP/Resend setting,
    not N individually bad addresses -- worth surfacing as a failed run."""
    _enable(db_session)
    monkeypatch.setattr(mailer, "is_configured", lambda *a, **k: True)
    monkeypatch.setattr(mailer, "send_email", lambda *a, **k: mailer.SendResult(sent=False, error="Connection refused"))
    _weekly_pick(db_session, "system_weekly_low", "Low Risk -- Weekly 10-Leg Accumulator (5-10 odds)")
    _user(db_session, "member@example.com")

    sys.argv = ["send_weekly_picks_email.py"]
    with pytest.raises(SystemExit) as exc:
        script.main()
    assert exc.value.code == 1
