"""The Live tab's sandbox used to write straight onto the real Match row
(score, status), so any user with access could push a fake result. It no
longer saves anything (see live_engine.simulate_live_event); these tests
cover both that and the admin-only clear endpoint, which stays to clean up
rows the old sandbox left behind.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app.timeutil import utcnow
from app.auth.passwords import hash_password
from app.auth.tokens import create_token
from app.config import get_settings
from app.db.models import AuditLog, Match, Team, User
from app.db.session import SessionLocal
from app.live_engine import record_live_event
from app.main import app

client = TestClient(app)


def _headers(user: User) -> dict:
    settings = get_settings()
    token = create_token({"user_id": user.id, "role": user.role}, settings.secret_key, settings.session_ttl_seconds)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def admin(db_session):
    user = User(
        email="live-admin@example.com",
        name="Live Admin",
        password_hash=hash_password("a-good-password"),
        role="superadmin",
        status="active",
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _seed_scheduled_match(db_session) -> Match:
    home = Team(name="Sandbox Home", league="League One")
    away = Team(name="Sandbox Away", league="League One")
    db_session.add_all([home, away])
    db_session.flush()

    match = Match(
        league="League One",
        season="2324",
        date=utcnow() + dt.timedelta(hours=6),
        home_team_id=home.id,
        away_team_id=away.id,
        status="SCHEDULED",
    )
    db_session.add(match)
    db_session.commit()
    db_session.refresh(match)
    return match


def _push_event(match_id: int, headers: dict) -> None:
    """Leaves the kind of row the old sandbox wrote -- through the
    persisting path, since the endpoint itself no longer saves anything."""

    del headers
    db = SessionLocal()
    try:
        record_live_event(db, db.get(Match, match_id), minute=60, score_home=1, score_away=0, trigger_event="goal")
    finally:
        db.close()


def test_sandbox_event_returns_a_projection_without_touching_the_match(db_session, admin, auth_headers):
    match = _seed_scheduled_match(db_session)

    response = client.post(
        f"/api/matches/{match.id}/live-event",
        json={"minute": 95, "score_home": 7, "score_away": 0, "trigger_event": "goal"},
        headers=_headers(admin),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["minute"] == 95
    assert body["score_home"] == 7
    assert 0.0 <= body["home_win"] <= 1.0

    db_session.refresh(match)
    assert match.status == "SCHEDULED"
    assert match.home_score is None
    assert match.away_score is None
    assert not match.is_finished
    assert client.get(f"/api/matches/{match.id}/live", headers=auth_headers).json() == []


@pytest.mark.parametrize(
    "payload",
    [
        {"minute": -1, "score_home": 0, "score_away": 0, "trigger_event": "goal"},
        {"minute": 200, "score_home": 0, "score_away": 0, "trigger_event": "goal"},
        {"minute": 60, "score_home": -3, "score_away": 0, "trigger_event": "goal"},
        {"minute": 60, "score_home": 0, "score_away": 0, "trigger_event": "not_an_event"},
    ],
)
def test_sandbox_rejects_impossible_states(db_session, admin, payload):
    match = _seed_scheduled_match(db_session)
    response = client.post(f"/api/matches/{match.id}/live-event", json=payload, headers=_headers(admin))
    assert response.status_code == 422, response.text


def test_the_sandbox_is_superadmin_only(db_session, auth_headers):
    match = _seed_scheduled_match(db_session)
    response = client.post(
        f"/api/matches/{match.id}/live-event",
        json={"minute": 60, "score_home": 1, "score_away": 0, "trigger_event": "goal"},
        headers=auth_headers,
    )
    assert response.status_code == 403


def test_superadmin_clears_a_pushed_simulated_event(db_session, admin, auth_headers):
    match = _seed_scheduled_match(db_session)
    _push_event(match.id, auth_headers)

    assert client.get(f"/api/matches/{match.id}/live", headers=auth_headers).json() != []
    db_session.refresh(match)
    assert match.status == "LIVE"
    assert match.home_score == 1

    response = client.delete(f"/api/admin/matches/{match.id}/live-events", headers=_headers(admin))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "SCHEDULED"
    assert body["home_score"] is None
    assert body["away_score"] is None

    assert client.get(f"/api/matches/{match.id}/live", headers=auth_headers).json() == []
    db_session.refresh(match)
    assert match.status == "SCHEDULED"
    assert match.home_score is None
    assert match.away_score is None


def test_clearing_live_events_is_recorded_in_the_audit_log(db_session, admin, auth_headers):
    match = _seed_scheduled_match(db_session)
    _push_event(match.id, auth_headers)

    client.delete(f"/api/admin/matches/{match.id}/live-events", headers=_headers(admin))

    entry = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "match.live_cleared")
        .order_by(AuditLog.id.desc())
        .first()
    )
    assert entry is not None
    assert entry.actor_user_id == admin.id
    assert entry.detail["match_id"] == match.id
    assert entry.detail["events_cleared"] == 1


def test_clearing_live_events_requires_superadmin(db_session, auth_headers):
    match = _seed_scheduled_match(db_session)
    _push_event(match.id, auth_headers)

    response = client.delete(f"/api/admin/matches/{match.id}/live-events", headers=auth_headers)
    assert response.status_code == 403

    assert client.delete(f"/api/admin/matches/{match.id}/live-events").status_code == 401


def test_clearing_a_match_with_no_live_events_is_a_harmless_no_op(db_session, admin):
    match = _seed_scheduled_match(db_session)

    response = client.delete(f"/api/admin/matches/{match.id}/live-events", headers=_headers(admin))
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "SCHEDULED"
    assert body["home_score"] is None


def test_clearing_an_unknown_match_is_404(admin):
    response = client.delete("/api/admin/matches/999999/live-events", headers=_headers(admin))
    assert response.status_code == 404
