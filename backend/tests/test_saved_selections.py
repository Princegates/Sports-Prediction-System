"""Saved selections: a member keeps picks to turn into a booking code later.

Only the picks are stored; using one runs it through /resolve, so today's
numbers are shown and games that have started are dropped. Everything here is
scoped to the owner -- another member's id is a plain 404.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.routes_betcodes import MAX_SAVED_SELECTIONS
from app.auth.passwords import hash_password
from app.auth.tokens import create_token
from app.config import get_settings
from app.db.models import Match, SavedSelection, Team, User
from app.main import app
from app.timeutil import utcnow
from tests.conftest import grant_active_access
from tests.test_betcode_routes import _prediction

client = TestClient(app)
PICK = {"market": "Match Result", "selection": "Home Win"}


@pytest.fixture()
def games(db_session):
    home = Team(name="Arsenal", league="English Premier League", aliases=[])
    away = Team(name="Chelsea", league="English Premier League", aliases=[])
    db_session.add_all([home, away])
    db_session.commit()
    made = []
    for i in range(1, 6):
        m = Match(
            league="English Premier League", season="2025-26", date=utcnow() + dt.timedelta(days=i),
            home_team_id=home.id, away_team_id=away.id, status="SCHEDULED",
        )
        db_session.add(m)
        db_session.commit()
        db_session.add(_prediction(m.id))
        db_session.commit()
        made.append(m)
    return made


def picks(*matches):
    return [{"match_id": m.id, **PICK} for m in matches]


def other_member(db_session, email="other@example.com"):
    user = User(email=email, name="Other", password_hash=hash_password("a-good-password"), role="user", status="active")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    grant_active_access(db_session, user)
    settings = get_settings()
    token = create_token({"user_id": user.id, "role": user.role}, settings.secret_key, settings.session_ttl_seconds)
    return {"Authorization": f"Bearer {token}"}


def test_saving_needs_a_signed_in_member_with_access(headers_no_access, games):
    assert client.get("/api/betcodes/saved").status_code == 401
    assert client.post("/api/betcodes/saved", json={"picks": picks(games[0])}).status_code == 401
    assert client.get("/api/betcodes/saved", headers=headers_no_access).status_code == 403
    assert client.post("/api/betcodes/saved", json={"picks": picks(games[0])}, headers=headers_no_access).status_code == 403


def test_a_saved_selection_comes_back_with_its_games(auth_headers, games):
    created = client.post(
        "/api/betcodes/saved", json={"name": "Weekend", "picks": picks(games[0], games[1])}, headers=auth_headers
    )
    assert created.status_code == 200
    assert created.json()["warnings"] == []

    listed = client.get("/api/betcodes/saved", headers=auth_headers).json()
    assert len(listed) == 1
    item = listed[0]
    assert item["name"] == "Weekend"
    assert item["upcoming_count"] == 2
    assert [(g["home_team"], g["away_team"], g["market"], g["selection"], g["upcoming"]) for g in item["games"]] == [
        ("Arsenal", "Chelsea", "Match Result", "Home Win", True)
    ] * 2


def test_a_blank_name_gets_a_default_and_spaces_are_tidied(auth_headers, games):
    blank = client.post("/api/betcodes/saved", json={"name": "   ", "picks": picks(games[0])}, headers=auth_headers)
    assert blank.json()["name"].startswith("Saved ")
    spaced = client.post(
        "/api/betcodes/saved", json={"name": "  my   slip  ", "picks": picks(games[1])}, headers=auth_headers
    )
    assert spaced.json()["name"] == "my slip"


def test_the_same_pick_twice_is_kept_once(auth_headers, games):
    created = client.post(
        "/api/betcodes/saved", json={"picks": picks(games[0], games[0])}, headers=auth_headers
    ).json()
    assert len(created["games"]) == 1


def test_picks_that_cannot_be_saved_are_reported_and_the_rest_kept(db_session, auth_headers, games):
    games[1].status = "FINISHED"
    db_session.commit()

    created = client.post(
        "/api/betcodes/saved", json={"picks": picks(games[0], games[1])}, headers=auth_headers
    ).json()

    assert [g["match_id"] for g in created["games"]] == [games[0].id]
    assert created["warnings"]  # says why the finished game was left out


def test_nothing_valid_to_save_is_refused(db_session, auth_headers, games):
    games[0].status = "FINISHED"
    db_session.commit()
    response = client.post("/api/betcodes/saved", json={"picks": picks(games[0])}, headers=auth_headers)
    assert response.status_code == 422

    unknown = client.post("/api/betcodes/saved", json={"picks": [{"match_id": 999999, **PICK}]}, headers=auth_headers)
    assert unknown.status_code == 422
    assert client.get("/api/betcodes/saved", headers=auth_headers).json() == []


def test_empty_and_oversized_requests_are_rejected(auth_headers, games):
    assert client.post("/api/betcodes/saved", json={"picks": []}, headers=auth_headers).status_code == 422
    too_many = [{"match_id": games[0].id, "market": f"Total Goals {i}", "selection": "Over"} for i in range(41)]
    assert client.post("/api/betcodes/saved", json={"picks": too_many}, headers=auth_headers).status_code == 422
    assert client.post(
        "/api/betcodes/saved", json={"name": "x" * 81, "picks": picks(games[0])}, headers=auth_headers
    ).status_code == 422


def test_there_is_a_limit_on_how_many_can_be_kept(db_session, auth_headers, games):
    user = db_session.execute(select(User).where(User.email == "test-user@example.com")).scalar_one()
    db_session.add_all(
        SavedSelection(user_id=user.id, name=f"s{i}", picks=[{"match_id": games[0].id, **PICK}])
        for i in range(MAX_SAVED_SELECTIONS)
    )
    db_session.commit()

    response = client.post("/api/betcodes/saved", json={"picks": picks(games[0])}, headers=auth_headers)

    assert response.status_code == 400
    assert str(MAX_SAVED_SELECTIONS) in response.json()["detail"]


def test_a_saved_selection_goes_stale_when_its_games_start(db_session, auth_headers, games):
    client.post("/api/betcodes/saved", json={"picks": picks(games[0], games[1], games[2])}, headers=auth_headers)

    games[0].status = "FINISHED"
    games[1].date = utcnow() - dt.timedelta(hours=1)  # kicked off
    db_session.commit()

    item = client.get("/api/betcodes/saved", headers=auth_headers).json()[0]
    assert item["upcoming_count"] == 1
    assert [g["upcoming"] for g in item["games"]] == [False, False, True]


def test_using_a_saved_selection_rechecks_it_against_todays_predictions(db_session, auth_headers, games):
    created = client.post("/api/betcodes/saved", json={"picks": picks(games[0], games[1])}, headers=auth_headers).json()
    games[1].status = "FINISHED"
    db_session.commit()
    saved_picks = [{"match_id": g["match_id"], "market": g["market"], "selection": g["selection"]} for g in created["games"]]

    resolved = client.post("/api/betcodes/resolve", json={"picks": saved_picks}, headers=auth_headers).json()

    assert [leg["match_id"] for leg in resolved["legs"]] == [games[0].id]
    assert resolved["warnings"]


def test_rename_and_delete(auth_headers, games):
    created = client.post("/api/betcodes/saved", json={"name": "Old", "picks": picks(games[0])}, headers=auth_headers).json()

    renamed = client.patch(f"/api/betcodes/saved/{created['id']}", json={"name": "New name"}, headers=auth_headers)
    assert renamed.status_code == 200 and renamed.json()["name"] == "New name"
    assert client.patch(f"/api/betcodes/saved/{created['id']}", json={"name": ""}, headers=auth_headers).status_code == 422

    assert client.delete(f"/api/betcodes/saved/{created['id']}", headers=auth_headers).status_code == 204
    assert client.get("/api/betcodes/saved", headers=auth_headers).json() == []
    assert client.delete(f"/api/betcodes/saved/{created['id']}", headers=auth_headers).status_code == 404


def test_members_only_ever_see_and_touch_their_own(db_session, auth_headers, games):
    mine = client.post("/api/betcodes/saved", json={"name": "Mine", "picks": picks(games[0])}, headers=auth_headers).json()
    theirs = other_member(db_session)

    assert client.get("/api/betcodes/saved", headers=theirs).json() == []
    assert client.patch(f"/api/betcodes/saved/{mine['id']}", json={"name": "Hijack"}, headers=theirs).status_code == 404
    assert client.delete(f"/api/betcodes/saved/{mine['id']}", headers=theirs).status_code == 404
    assert client.get("/api/betcodes/saved", headers=auth_headers).json()[0]["name"] == "Mine"


def test_deleting_an_account_removes_its_saved_selections(db_session, games):
    from tests.test_admin_hardening import _headers, _make_user

    admin = _make_user(db_session, "admin@example.com", role="superadmin")
    target = _make_user(db_session, "gone@example.com")
    db_session.add(SavedSelection(user_id=target.id, name="x", picks=[{"match_id": games[0].id, **PICK}]))
    db_session.commit()
    target_id = target.id

    assert client.delete(f"/api/admin/users/{target_id}", headers=_headers(admin)).status_code == 200

    db_session.expire_all()
    assert db_session.execute(select(SavedSelection).where(SavedSelection.user_id == target_id)).first() is None
