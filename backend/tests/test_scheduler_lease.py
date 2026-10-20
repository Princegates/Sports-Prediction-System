from __future__ import annotations

import datetime as dt

from app import scheduler_lease

TTL = dt.timedelta(minutes=10)
NOW = dt.datetime(2026, 10, 20, 12, 0)


def test_first_caller_takes_the_lease_and_others_are_refused(db_session):
    assert scheduler_lease.acquire(db_session, "live_sync", TTL, holder="a", now=NOW)
    assert not scheduler_lease.acquire(db_session, "live_sync", TTL, holder="b", now=NOW + dt.timedelta(minutes=1))


def test_the_holder_renews(db_session):
    assert scheduler_lease.acquire(db_session, "live_sync", TTL, holder="a", now=NOW)
    assert scheduler_lease.acquire(db_session, "live_sync", TTL, holder="a", now=NOW + dt.timedelta(minutes=9))
    # Renewed at 12:09, so still held at 12:15.
    assert not scheduler_lease.acquire(db_session, "live_sync", TTL, holder="b", now=NOW + dt.timedelta(minutes=15))


def test_an_expired_lease_passes_to_the_next_caller(db_session):
    assert scheduler_lease.acquire(db_session, "live_sync", TTL, holder="a", now=NOW)
    assert scheduler_lease.acquire(db_session, "live_sync", TTL, holder="b", now=NOW + dt.timedelta(minutes=11))
    assert not scheduler_lease.acquire(db_session, "live_sync", TTL, holder="a", now=NOW + dt.timedelta(minutes=12))


def test_leases_are_independent_by_name(db_session):
    assert scheduler_lease.acquire(db_session, "live_sync", TTL, holder="a", now=NOW)
    assert scheduler_lease.acquire(db_session, "lineup_check", TTL, holder="b", now=NOW)
