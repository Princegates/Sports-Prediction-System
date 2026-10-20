"""Scheduling of app.main's background loops: one runner per loop across
processes (scheduler lease), and long sleeps through quiet stretches so the
database can scale to zero between fixtures."""

from __future__ import annotations

import datetime as dt

import pytest

from app.timeutil import utcnow
from app import main, scheduler_lease
from app.db.models import Match, Team


@pytest.fixture()
def teams(db_session):
    home, away = Team(name="Loop Home", league="L"), Team(name="Loop Away", league="L")
    db_session.add_all([home, away])
    db_session.commit()
    return home, away


def _fixture(db_session, teams, kickoff_in: dt.timedelta, status: str = "SCHEDULED") -> None:
    home, away = teams
    db_session.add(Match(league="L", season="2526", date=utcnow() + kickoff_in,
                         home_team_id=home.id, away_team_id=away.id, status=status))
    db_session.commit()


def test_no_upcoming_fixture_sleeps_the_maximum(db_session):
    assert main._seconds_until_next_kickoff_window(db_session, dt.timedelta(minutes=10), 300) == main.IDLE_RECHECK_MAX_SECONDS


def test_a_distant_fixture_is_capped_at_an_hour(db_session, teams):
    _fixture(db_session, teams, dt.timedelta(hours=9))
    assert main._seconds_until_next_kickoff_window(db_session, dt.timedelta(minutes=10), 300) == main.IDLE_RECHECK_MAX_SECONDS


def test_wakes_shortly_before_the_next_kickoff(db_session, teams):
    _fixture(db_session, teams, dt.timedelta(minutes=40))
    delay = main._seconds_until_next_kickoff_window(db_session, dt.timedelta(minutes=10), 300)
    assert 29 * 60 <= delay <= 30 * 60


def test_never_sleeps_less_than_the_normal_interval(db_session, teams):
    _fixture(db_session, teams, dt.timedelta(minutes=3))
    assert main._seconds_until_next_kickoff_window(db_session, dt.timedelta(minutes=10), 300) == 300


def test_loop_work_is_skipped_while_another_process_holds_the_lease(db_session):
    scheduler_lease.acquire(db_session, "live_sync", main.LEASE_TTL, holder="some-other-process")
    calls = []

    delay = main._run_if_lease_held("live_sync", lambda: calls.append(1) or 123.0, main.LEASE_TTL)

    assert calls == []
    assert delay == main.LEASE_RETRY_SECONDS


def test_loop_work_runs_once_the_lease_is_free(db_session):
    calls = []
    assert main._run_if_lease_held("live_sync", lambda: calls.append(1) or 123.0, main.LEASE_TTL) == 123.0
    assert calls == [1]

    main._release_leases()
    assert scheduler_lease.acquire(db_session, "live_sync", main.LEASE_TTL, holder="next-instance")


def test_live_sync_with_no_key_sleeps_until_the_next_window(db_session, teams):
    _fixture(db_session, teams, dt.timedelta(minutes=70))
    delay = main._run_live_sync_once()
    assert 59 * 60 <= delay <= 60 * 60
