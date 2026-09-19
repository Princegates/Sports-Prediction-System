"""A pushed live event writes straight onto the real Match row (score,
status), and any user with access can push one from the Live tab's sandbox
-- so a superadmin needs a way to undo it. These tests cover the admin-only
clear endpoint added for exactly that.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app.auth.passwords import hash_password
from app.auth.tokens import create_token
from app.config import get_settings
from app.db.models import AuditLog, Match, Team, User
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
        date=dt.datetime.utcnow() + dt.timedelta(hours=6),
        home_team_id=home.id,
        away_team_id=away.id,
        status="SCHEDULED",
    )
    db_session.add(match)
    db_session.commit()
    db_session.refresh(match)
    return match


def _push_event(match_id: int, headers: dict) -> None:
    response = client.post(
        f"/api/matches/{match_id}/live-event",
        json={"minute": 60, "score_home": 1, "score_away": 0, "trigger_event": "goal"},
        headers=headers,
    )
    assert response.status_code == 200, response.text


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
