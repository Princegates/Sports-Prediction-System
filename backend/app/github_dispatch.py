"""Starts the GitHub "Refresh predictions" workflow from this server, on time.

Why this exists: the workflow's own ``schedule:`` trigger is unreliable --
GitHub starts scheduled runs hours late (a 06:00 UTC job has begun at 10:52 and
at 13:49), so the day's predictions and odds arrived long after the matches
they were for. A ``workflow_dispatch`` request starts immediately, and this
server already runs a scheduler (app.main's loops, one runner across all
workers thanks to app.scheduler_lease). So the server asks GitHub to start the
run at the right time, and GitHub still does the heavy work -- the trained
models live in its cache, not here.

Two slots, matching the old cron lines:

    refresh   every day 06:00 UTC       (the daily refresh)
    retrain   Mondays   03:00 UTC       (the weekly retrain)

Safety, in order of importance:

* Off by default, and needs a token an admin supplies in Settings.
* It never starts a slot twice: each attempt is written to the Activity Log
  (AuditLog) and consulted before the next, and GitHub itself is asked whether
  a run for that slot is already queued, running or finished.
* The workflow's own schedule stays on as a backup. If this server was down at
  06:00, GitHub's late run still happens -- and if this server already started
  one, that late run skips itself (see the workflow's ``setup`` job).
* A failed attempt is retried a few times, spaced out, then left alone with
  the reason in the Activity Log.
* The token is never logged or returned.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

import requests
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import app_settings
from app.db.models import AuditLog
from app.timeutil import utcnow

logger = logging.getLogger(__name__)

API = "https://api.github.com"
WORKFLOW_FILE = "refresh-predictions.yml"
TIMEOUT_SECONDS = 15

# A slot can still be started this long after its time (server was down; a
# late start still beats none). After that the next slot takes over.
WINDOW = dt.timedelta(hours=18)
# A run that GitHub (or a person) started up to this long before the slot
# counts as this slot's run.
EARLY_TOLERANCE = dt.timedelta(hours=1)
MAX_ATTEMPTS = 3
RETRY_AFTER = dt.timedelta(minutes=15)

_ACTIVE = {"queued", "in_progress", "waiting", "pending", "requested"}


@dataclass(frozen=True)
class Slot:
    mode: str  # a mode the workflow's workflow_dispatch input accepts
    hour: int
    minute: int = 0
    weekday: int | None = None  # Monday = 0; None = every day


SLOTS: tuple[Slot, ...] = (
    Slot("retrain", hour=3, weekday=0),
    Slot("refresh", hour=6),
)


class GitHubError(RuntimeError):
    """GitHub couldn't be reached or refused the request. The message is safe
    to show an admin and never contains the token."""


@dataclass(frozen=True)
class DispatchConfig:
    enabled: bool
    token: str
    repo: str
    ref: str  # blank = the repository's default branch

    @property
    def usable(self) -> bool:
        return bool(self.token) and "/" in self.repo


def resolve_config(db: Session) -> DispatchConfig:
    return DispatchConfig(
        enabled=bool(app_settings.get_value(db, "github_dispatch_enabled")),
        token=str(app_settings.get_value(db, "github_dispatch_token") or "").strip(),
        repo=str(app_settings.get_value(db, "github_dispatch_repo") or "").strip().strip("/"),
        ref=str(app_settings.get_value(db, "github_dispatch_ref") or "").strip(),
    )


# --- Which slots are due -------------------------------------------------------


def slot_start(slot: Slot, day: dt.date) -> dt.datetime | None:
    """When ``slot`` is due on ``day`` (naive UTC), or None if it doesn't run that day."""

    if slot.weekday is not None and day.weekday() != slot.weekday:
        return None
    return dt.datetime.combine(day, dt.time(slot.hour, slot.minute))


def due_slots(now: dt.datetime) -> list[tuple[Slot, dt.datetime]]:
    """Slots whose time has passed and that are still within WINDOW of it."""

    due: list[tuple[Slot, dt.datetime]] = []
    for slot in SLOTS:
        for day in (now.date() - dt.timedelta(days=1), now.date()):
            start = slot_start(slot, day)
            if start is not None and start <= now < start + WINDOW:
                due.append((slot, start))
    return sorted(due, key=lambda item: item[1])


# --- GitHub ----------------------------------------------------------------------


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "soccaintel-scheduler",
    }


def _call(config: DispatchConfig, method: str, path: str, *, json=None, params=None) -> requests.Response:
    try:
        response = requests.request(
            method, f"{API}{path}", headers=_headers(config.token), json=json, params=params, timeout=TIMEOUT_SECONDS
        )
    except requests.RequestException as exc:
        raise GitHubError(f"Couldn't reach GitHub ({type(exc).__name__}).") from exc
    if response.status_code in (401, 403):
        raise GitHubError(
            "GitHub refused the token. Check it hasn't expired and has Actions read and write access to this repository."
        )
    if response.status_code == 404:
        raise GitHubError("GitHub can't find the repository or the workflow with this token. Check the repository name.")
    if response.status_code >= 400:
        raise GitHubError(f"GitHub answered {response.status_code}: {response.text[:150]}")
    return response


def default_branch(config: DispatchConfig) -> str:
    branch = _call(config, "GET", f"/repos/{config.repo}").json().get("default_branch")
    if not branch:
        raise GitHubError("GitHub didn't say which branch is the default.")
    return str(branch)


def existing_run(config: DispatchConfig, mode: str, since: dt.datetime) -> dict | None:
    """A queued, running or successful run for ``mode`` created since ``since``.
    The workflow names each run ``... mode:<mode>`` so this can tell a refresh
    from a retrain. Cancelled and failed runs don't count: those are worth
    starting again."""

    runs = _call(
        config,
        "GET",
        f"/repos/{config.repo}/actions/workflows/{WORKFLOW_FILE}/runs",
        params={"created": f">={since:%Y-%m-%dT%H:%M:%SZ}", "per_page": 50},
    ).json().get("workflow_runs", [])
    for run in runs:
        if f"mode:{mode}" not in str(run.get("display_title") or ""):
            continue
        if run.get("status") in _ACTIVE or run.get("conclusion") == "success":
            return run
    return None


def dispatch(config: DispatchConfig, mode: str, ref: str) -> None:
    _call(
        config,
        "POST",
        f"/repos/{config.repo}/actions/workflows/{WORKFLOW_FILE}/dispatches",
        json={"ref": ref, "inputs": {"mode": mode}},
    )


def check_connection(config: DispatchConfig) -> tuple[bool, str]:
    """For the admin's "Check GitHub connection" button."""

    if not config.token:
        return False, "Add a token first."
    if "/" not in config.repo:
        return False, "Enter the repository as owner/name, for example Princegates/Sports-Prediction-System."
    try:
        branch = default_branch(config)
        _call(config, "GET", f"/repos/{config.repo}/actions/workflows/{WORKFLOW_FILE}")
    except GitHubError as exc:
        return False, str(exc)
    return True, (
        f"Connected to {config.repo} (default branch: {branch}) and found the refresh workflow. "
        "Starting a run also needs the token to have Actions: write; that is only tested for real at the next slot."
    )


# --- The scheduler's side ---------------------------------------------------------


def _action(mode: str, outcome: str) -> str:
    return f"automation.{mode}_{outcome}"


def _history(db: Session, slot: Slot, start: dt.datetime) -> tuple[bool, int, dt.datetime | None]:
    """(already handled, failed attempts, last failure) for this slot since it was due."""

    since = start - EARLY_TOLERANCE
    handled = db.execute(
        select(func.count(AuditLog.id)).where(
            AuditLog.action.in_([_action(slot.mode, "started"), _action(slot.mode, "already_running")]),
            AuditLog.created_at >= since,
        )
    ).scalar_one()
    failures, last = db.execute(
        select(func.count(AuditLog.id), func.max(AuditLog.created_at)).where(
            AuditLog.action == _action(slot.mode, "failed"), AuditLog.created_at >= since
        )
    ).one()
    return handled > 0, failures, last


def _record(db: Session, slot: Slot, outcome: str, detail: dict, now: dt.datetime) -> None:
    # Stamped with the pass's own clock (not the database default) so "has this
    # slot been handled?" compares like with like.
    db.add(
        AuditLog(actor_user_id=None, actor_email=None, action=_action(slot.mode, outcome), detail=detail, created_at=now)
    )
    db.commit()


def check_and_dispatch(db: Session, now: dt.datetime | None = None) -> list[str]:
    """One pass of the scheduler: start whatever is due and not yet started.
    Returns a line per thing it did, for the log."""

    now = now or utcnow()
    config = resolve_config(db)
    if not config.enabled or not config.usable:
        return []

    lines: list[str] = []
    for slot, start in due_slots(now):
        handled, failures, last_failure = _history(db, slot, start)
        if handled or failures >= MAX_ATTEMPTS:
            continue
        if last_failure is not None and now - last_failure < RETRY_AFTER:
            continue
        try:
            found = existing_run(config, slot.mode, start - EARLY_TOLERANCE)
            if found is not None:
                _record(db, slot, "already_running", {"run": found.get("html_url"), "status": found.get("status")}, now)
                lines.append(f"{slot.mode}: a run for today already exists -- left alone")
                continue
            ref = config.ref or default_branch(config)
            dispatch(config, slot.mode, ref)
            _record(db, slot, "started", {"ref": ref, "slot": f"{start:%Y-%m-%d %H:%M} UTC"}, now)
            lines.append(f"{slot.mode}: started on {ref}")
        except GitHubError as exc:
            _record(db, slot, "failed", {"error": str(exc)[:200], "attempt": failures + 1}, now)
            lines.append(f"{slot.mode}: failed ({exc})")
    return lines
