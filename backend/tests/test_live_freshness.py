"""A match's status column can say LIVE for two reasons that should never be
treated the same: a real fixture the sync job just confirmed is in progress,
or anyone with access pushing a simulated event from that match's own Live
tab (a local demo of the recalculation, never meant to broadcast). Both set
status="LIVE"; only the first also stamps live_synced_at. These tests pin
that "genuinely live" -- the Live Match Center, the Dashboard, and Guda's
own live_matches() -- means a *recent* sync confirmation, not the status
column alone.
"""

from __future__ import annotations

import datetime as dt

from fastapi.testclient import TestClient

from app.assistant.retrieval import live_matches
from app.db.models import Match, Team
from app.live_engine import LIVE_STALE_AFTER, is_genuinely_live
from app.main import app

client = TestClient(app)


def _seed_match(db_session, *, status: str, live_synced_at: dt.datetime | None) -> Match:
    home = Team(name="Freshness Home", league="League One")
    away = Team(name="Freshness Away", league="League One")
    db_session.add_all([home, away])
    db_session.flush()

    match = Match(
        league="League One",
        season="2324",
        date=dt.datetime.utcnow(),
        home_team_id=home.id,
        away_team_id=away.id,
        status=status,
        live_synced_at=live_synced_at,
    )
    db_session.add(match)
    db_session.commit()
    db_session.refresh(match)
    return match


# --- is_genuinely_live -------------------------------------------------------


def test_a_fresh_sync_confirmation_is_genuinely_live(db_session):
    match = _seed_match(db_session, status="LIVE", live_synced_at=dt.datetime.utcnow())
    assert is_genuinely_live(match) is True


def test_a_simulated_match_with_no_sync_stamp_is_not_genuinely_live(db_session):
    match = _seed_match(db_session, status="LIVE", live_synced_at=None)
    assert is_genuinely_live(match) is False


def test_a_stale_sync_stamp_is_not_genuinely_live(db_session):
    stale = dt.datetime.utcnow() - LIVE_STALE_AFTER - dt.timedelta(minutes=1)
    match = _seed_match(db_session, status="LIVE", live_synced_at=stale)
    assert is_genuinely_live(match) is False


def test_a_scheduled_match_is_never_genuinely_live_even_with_a_stamp(db_session):
    match = _seed_match(db_session, status="SCHEDULED", live_synced_at=dt.datetime.utcnow())
    assert is_genuinely_live(match) is False


# --- /api/matches?status=LIVE ------------------------------------------------


def test_matches_endpoint_excludes_a_simulated_match(db_session, auth_headers):
    _seed_match(db_session, status="LIVE", live_synced_at=None)
    response = client.get("/api/matches", params={"status": "LIVE"}, headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == []


def test_matches_endpoint_excludes_a_stale_match(db_session, auth_headers):
    stale = dt.datetime.utcnow() - LIVE_STALE_AFTER - dt.timedelta(minutes=1)
    _seed_match(db_session, status="LIVE", live_synced_at=stale)
    response = client.get("/api/matches", params={"status": "LIVE"}, headers=auth_headers)
    assert response.json() == []


def test_matches_endpoint_includes_a_freshly_synced_match(db_session, auth_headers):
    match = _seed_match(db_session, status="LIVE", live_synced_at=dt.datetime.utcnow())
    response = client.get("/api/matches", params={"status": "LIVE"}, headers=auth_headers)
    body = response.json()
    assert len(body) == 1
    assert body[0]["id"] == match.id
    assert body[0]["is_live"] is True


def test_matches_endpoint_reports_is_live_false_for_a_simulated_match(db_session, auth_headers):
    """Fetched without the status filter (as the Dashboard's date-range
    queries do), so a simulated match still appears -- but its own is_live
    field is what a page should trust, not its raw status column."""

    match = _seed_match(db_session, status="LIVE", live_synced_at=None)
    response = client.get("/api/matches", params={"league": "League One"}, headers=auth_headers)
    body = response.json()
    row = next(m for m in body if m["id"] == match.id)
    assert row["status"] == "LIVE"
    assert row["is_live"] is False


def test_other_statuses_are_unaffected_by_the_freshness_gate(db_session, auth_headers):
    _seed_match(db_session, status="SCHEDULED", live_synced_at=None)
    response = client.get("/api/matches", params={"status": "SCHEDULED"}, headers=auth_headers)
    assert len(response.json()) == 1


# --- Guda's live_matches() ---------------------------------------------------


def test_assistant_live_matches_excludes_a_simulated_match(db_session):
    _seed_match(db_session, status="LIVE", live_synced_at=None)
    assert live_matches(db_session) == []


def test_assistant_live_matches_includes_a_freshly_synced_match(db_session):
    match = _seed_match(db_session, status="LIVE", live_synced_at=dt.datetime.utcnow())
    cards = live_matches(db_session)
    assert len(cards) == 1
    assert cards[0].match_id == match.id
