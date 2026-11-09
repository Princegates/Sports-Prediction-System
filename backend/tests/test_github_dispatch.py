"""The server starts the GitHub refresh workflow itself, on time, because
GitHub's own schedule starts hours late. It must start each slot once, never
double up on a run that already exists, retry failures sparingly, and never
leak the token."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
import requests
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import app_settings, github_dispatch
from app.db.models import AuditLog
from app.main import _LOOPS, app
from tests.test_admin_hardening import _headers, _make_user

client = TestClient(app)
TOKEN = "github_pat_SECRET_TOKEN_VALUE"
REPO = "Princegates/Sports-Prediction-System"
TUESDAY = dt.datetime(2026, 11, 10, 6, 5)   # just after the daily slot
MONDAY = dt.datetime(2026, 11, 9, 3, 10)    # just after the Monday retrain slot


class FakeResponse:
    def __init__(self, status=200, body=None, text=""):
        self.status_code, self._body, self.text = status, body if body is not None else {}, text

    def json(self):
        return self._body


class FakeGitHub:
    """Scripted GitHub: records every call and answers by method + path."""

    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []
        self.runs: list[dict] = []
        self.dispatch_status = 204
        self.default_branch = "main"
        self.repo_status = 200

    def __call__(self, method, url, headers=None, json=None, params=None, timeout=None):
        assert headers["Authorization"] == f"Bearer {TOKEN}"
        path = url.removeprefix(github_dispatch.API)
        self.calls.append((method, path, json or params or {}))
        if method == "POST" and path.endswith("/dispatches"):
            return FakeResponse(self.dispatch_status, text="nope")
        if path.endswith("/runs"):
            return FakeResponse(200, {"workflow_runs": self.runs})
        if path.endswith(github_dispatch.WORKFLOW_FILE):
            return FakeResponse(200, {"id": 1})
        if path == f"/repos/{REPO}":
            return FakeResponse(self.repo_status, {"default_branch": self.default_branch})
        return FakeResponse(404)

    def dispatches(self):
        return [c for c in self.calls if c[0] == "POST"]


@pytest.fixture()
def gh(monkeypatch):
    fake = FakeGitHub()
    monkeypatch.setattr(github_dispatch.requests, "request", fake)
    return fake


@pytest.fixture()
def enabled(db_session):
    app_settings.set_values(
        db_session,
        {"github_dispatch_enabled": True, "github_dispatch_token": TOKEN, "github_dispatch_repo": REPO},
    )


def run(title, status="completed", conclusion="success"):
    return {"display_title": title, "status": status, "conclusion": conclusion, "html_url": "https://github.com/x/runs/1"}


def audit(db_session, outcome=None):
    rows = db_session.execute(select(AuditLog).where(AuditLog.action.like("automation.%"))).scalars().all()
    return [r for r in rows if outcome is None or r.action.endswith(outcome)]


# --- which slots are due ------------------------------------------------------------


def test_the_daily_refresh_is_due_after_six_utc_and_not_before():
    assert github_dispatch.due_slots(dt.datetime(2026, 11, 10, 5, 59)) == []
    due = github_dispatch.due_slots(TUESDAY)
    assert [s.mode for s, _ in due] == ["refresh"]


def test_the_weekly_retrain_is_due_on_mondays_only():
    assert [s.mode for s, _ in github_dispatch.due_slots(MONDAY)] == ["retrain"]
    # Monday after 06:00: the retrain slot is still open (18h) and the refresh is due too.
    monday_morning = dt.datetime(2026, 11, 9, 6, 30)
    assert [s.mode for s, _ in github_dispatch.due_slots(monday_morning)] == ["retrain", "refresh"]
    # Tuesday 05:00: both of yesterday's windows have closed, today's refresh isn't due yet.
    assert github_dispatch.due_slots(dt.datetime(2026, 11, 10, 5, 0)) == []


def test_a_slot_missed_while_the_server_was_down_is_still_started_late_within_the_window():
    late = dt.datetime(2026, 11, 10, 14, 0)  # server came back at 14:00
    assert [s.mode for s, _ in github_dispatch.due_slots(late)] == ["refresh"]
    too_late = dt.datetime(2026, 11, 11, 0, 30)  # 18h30 after the slot
    assert github_dispatch.due_slots(too_late) == []


# --- doing nothing safely -----------------------------------------------------------


def test_it_does_nothing_unless_switched_on_and_given_a_token(db_session, gh):
    assert github_dispatch.check_and_dispatch(db_session, TUESDAY) == []
    app_settings.set_values(db_session, {"github_dispatch_enabled": True})  # on, but no token
    assert github_dispatch.check_and_dispatch(db_session, TUESDAY) == []
    assert gh.calls == [] and audit(db_session) == []


# --- starting a run -----------------------------------------------------------------


def test_it_starts_the_daily_refresh_once(db_session, gh, enabled):
    first = github_dispatch.check_and_dispatch(db_session, TUESDAY)
    again = github_dispatch.check_and_dispatch(db_session, TUESDAY + dt.timedelta(minutes=5))

    assert first == ["refresh: started on main"]
    assert again == []
    assert gh.dispatches() == [("POST", f"/repos/{REPO}/actions/workflows/refresh-predictions.yml/dispatches",
                                {"ref": "main", "inputs": {"mode": "refresh"}})]
    assert [r.action for r in audit(db_session)] == ["automation.refresh_started"]


def test_a_configured_branch_is_used_without_asking_github_for_the_default(db_session, gh, enabled):
    app_settings.set_values(db_session, {"github_dispatch_ref": "release"})

    github_dispatch.check_and_dispatch(db_session, TUESDAY)

    assert gh.dispatches()[0][2]["ref"] == "release"
    assert not any(c[1] == f"/repos/{REPO}" for c in gh.calls)


def test_monday_starts_the_retrain_then_the_refresh(db_session, gh, enabled):
    github_dispatch.check_and_dispatch(db_session, MONDAY)
    github_dispatch.check_and_dispatch(db_session, dt.datetime(2026, 11, 9, 6, 10))

    assert [c[2]["inputs"]["mode"] for c in gh.dispatches()] == ["retrain", "refresh"]


# --- never doubling up --------------------------------------------------------------


@pytest.mark.parametrize(
    "existing",
    [
        run("Refresh predictions mode:refresh", status="queued", conclusion=None),
        run("Refresh predictions mode:refresh", status="in_progress", conclusion=None),
        run("Refresh predictions mode:refresh", status="completed", conclusion="success"),
    ],
)
def test_a_run_already_queued_running_or_done_is_left_alone(db_session, gh, enabled, existing):
    gh.runs = [existing]

    lines = github_dispatch.check_and_dispatch(db_session, TUESDAY)

    assert gh.dispatches() == []
    assert lines == ["refresh: a run for today already exists -- left alone"]
    assert [r.action for r in audit(db_session)] == ["automation.refresh_already_running"]
    # ...and it doesn't ask again on the next pass.
    github_dispatch.check_and_dispatch(db_session, TUESDAY + dt.timedelta(minutes=5))
    assert len([c for c in gh.calls if c[1].endswith("/runs")]) == 1


def test_a_failed_or_cancelled_run_does_not_count_so_it_is_started_again(db_session, gh, enabled):
    gh.runs = [
        run("Refresh predictions mode:refresh", conclusion="cancelled"),
        run("Refresh predictions mode:refresh", conclusion="failure"),
    ]
    github_dispatch.check_and_dispatch(db_session, TUESDAY)
    assert len(gh.dispatches()) == 1


def test_a_run_of_the_other_kind_does_not_count(db_session, gh, enabled):
    gh.runs = [run("Refresh predictions mode:retrain", status="in_progress", conclusion=None)]
    github_dispatch.check_and_dispatch(db_session, TUESDAY)
    assert [c[2]["inputs"]["mode"] for c in gh.dispatches()] == ["refresh"]


# --- failing safely -----------------------------------------------------------------


def test_a_refused_token_is_recorded_without_the_token_and_retried_sparingly(db_session, gh, enabled):
    gh.dispatch_status = 403

    github_dispatch.check_and_dispatch(db_session, TUESDAY)
    github_dispatch.check_and_dispatch(db_session, TUESDAY + dt.timedelta(minutes=5))   # too soon: no retry
    assert len(gh.dispatches()) == 1
    github_dispatch.check_and_dispatch(db_session, TUESDAY + dt.timedelta(minutes=20))  # spaced out: retry
    github_dispatch.check_and_dispatch(db_session, TUESDAY + dt.timedelta(minutes=40))
    github_dispatch.check_and_dispatch(db_session, TUESDAY + dt.timedelta(hours=2))     # capped at 3 attempts
    assert len(gh.dispatches()) == 3

    rows = audit(db_session, "failed")
    assert len(rows) == 3
    assert "refused the token" in rows[0].detail["error"]
    assert TOKEN not in str(rows[0].detail) and TOKEN not in rows[0].action


def test_an_unreachable_github_is_a_recorded_failure_not_a_crash(db_session, enabled, monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("dns failure with secret " + TOKEN)

    monkeypatch.setattr(github_dispatch.requests, "request", boom)

    lines = github_dispatch.check_and_dispatch(db_session, TUESDAY)

    assert lines and "failed" in lines[0]
    assert TOKEN not in str(lines) and TOKEN not in str([r.detail for r in audit(db_session)])


# --- the admin's connection check ---------------------------------------------------


def test_check_connection_reports_a_good_token(db_session, gh, enabled):
    ok, detail = github_dispatch.check_connection(github_dispatch.resolve_config(db_session))
    assert ok and "default branch: main" in detail


def test_check_connection_explains_a_bad_token_or_repo(db_session, gh, enabled):
    gh.repo_status = 404
    ok, detail = github_dispatch.check_connection(github_dispatch.resolve_config(db_session))
    assert not ok and "can't find" in detail

    app_settings.set_values(db_session, {"github_dispatch_token": "", "github_dispatch_repo": "nonsense"})
    assert github_dispatch.check_connection(github_dispatch.resolve_config(db_session)) == (False, "Add a token first.")


def test_the_check_endpoint_is_admin_only_and_never_returns_the_token(db_session, gh, enabled):
    admin = _make_user(db_session, "admin@example.com", role="superadmin")
    plain = _make_user(db_session, "plain@example.com")

    assert client.post("/api/admin/settings/test-github", headers=_headers(plain)).status_code == 403
    response = client.post("/api/admin/settings/test-github", headers=_headers(admin))

    assert response.status_code == 200 and response.json()["ok"] is True
    assert TOKEN not in response.text


def test_the_token_setting_is_a_write_only_secret(db_session, enabled):
    admin = _make_user(db_session, "admin2@example.com", role="superadmin")
    payload = client.get("/api/admin/settings", headers=_headers(admin)).json()

    assert payload["secrets_set"]["github_dispatch_token"] is True
    assert TOKEN not in str(payload)
    assert "automation" in payload["groups"]


# --- wiring -------------------------------------------------------------------------


def test_the_scheduler_runs_the_loop():
    assert "github_dispatch" in {loop[0] for loop in _LOOPS}


WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "refresh-predictions.yml"


def test_the_workflow_names_runs_by_mode_and_its_backup_schedule_can_skip():
    workflow = yaml.safe_load(WORKFLOW.read_text())
    # The server finds today's run by this marker in the run title.
    assert "mode:" in workflow["run-name"]
    # The late scheduled copy checks for an on-time run and skips itself...
    assert workflow["jobs"]["setup"]["permissions"] == {"actions": "read"}
    # ...which only works if no job runs for the "skip" mode.
    assert "'skip'" not in workflow["jobs"]["refresh"]["if"]
    assert "mode == 'refresh'" in workflow["jobs"]["refresh"]["if"]
    for name, job in workflow["jobs"].items():
        if name.startswith("retrain-"):
            assert job["if"] == "needs.setup.outputs.mode == 'retrain'"
    # The backup schedule is still there.
    assert workflow[True]["schedule"]
