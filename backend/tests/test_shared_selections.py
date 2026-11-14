"""Sharing a slip: a member makes a link to the picks of a generated slip; another
member opens it, edits it, and only then makes their own booking code.

The share is a copy of the picks at that moment, tied to nobody's account in
what the recipient sees (first name only), and stops working after two weeks."""

from __future__ import annotations

import datetime as dt

from fastapi.testclient import TestClient
from sqlalchemy import select, text

from tests.conftest import sqlite_foreign_keys
from app.api.routes_betcodes import MAX_ACTIVE_SHARES, SHARE_LINK_DAYS
from app.db.models import AuditLog, SavedSelection, SharedSelection, User
from app.main import app
from app.timeutil import utcnow
from tests.test_saved_selections import games, other_member, picks  # noqa: F401  (fixtures + helpers)

client = TestClient(app)


def _share(headers, *matches, name=None):
    body = {"picks": picks(*matches)}
    if name is not None:
        body["name"] = name
    return client.post("/api/betcodes/shared", json=body, headers=headers)


def test_sharing_and_opening_need_a_signed_in_member_with_access(headers_no_access, auth_headers, games):
    assert client.post("/api/betcodes/shared", json={"picks": picks(games[0])}).status_code == 401
    assert client.get("/api/betcodes/shared/abc").status_code == 401
    assert _share(headers_no_access, games[0]).status_code == 403
    token = _share(auth_headers, games[0]).json()["token"]
    assert client.get(f"/api/betcodes/shared/{token}", headers=headers_no_access).status_code == 403


def test_another_member_opens_what_was_shared(db_session, auth_headers, games):
    created = _share(auth_headers, games[0], games[1], name="Weekend banker")
    assert created.status_code == 200
    body = created.json()
    assert body["warnings"] == [] and len(body["token"]) >= 20
    expires = dt.datetime.fromisoformat(body["expires_at"]) - dt.datetime.fromisoformat(body["created_at"])
    assert abs(expires - dt.timedelta(days=SHARE_LINK_DAYS)) < dt.timedelta(minutes=1)

    friend = other_member(db_session)
    opened = client.get(f"/api/betcodes/shared/{body['token']}", headers=friend)
    assert opened.status_code == 200
    got = opened.json()
    assert got["name"] == "Weekend banker" and got["upcoming_count"] == 2
    assert [(g["home_team"], g["away_team"], g["market"], g["selection"], g["upcoming"]) for g in got["games"]] == [
        ("Arsenal", "Chelsea", "Match Result", "Home Win", True)
    ] * 2


def test_the_recipient_sees_a_first_name_only_never_an_email(db_session, auth_headers, games):
    owner = db_session.execute(select(User).where(User.email == "test-user@example.com")).scalar_one()
    owner.name = "Ada Lovelace"
    db_session.commit()
    token = _share(auth_headers, games[0]).json()["token"]

    got = client.get(f"/api/betcodes/shared/{token}", headers=other_member(db_session)).json()

    assert got["shared_by"] == "Ada"
    assert "test-user@example.com" not in str(got) and "Lovelace" not in str(got)


def test_a_member_with_no_name_is_shared_without_one(db_session, auth_headers, games):
    owner = db_session.execute(select(User).where(User.email == "test-user@example.com")).scalar_one()
    owner.name = "   "
    db_session.commit()
    token = _share(auth_headers, games[0]).json()["token"]
    assert client.get(f"/api/betcodes/shared/{token}", headers=other_member(db_session)).json()["shared_by"] is None


def test_the_share_is_a_copy_the_sender_cannot_change_underneath_the_recipient(db_session, auth_headers, games):
    client.post("/api/betcodes/saved", json={"picks": picks(games[0], games[1])}, headers=auth_headers)
    token = _share(auth_headers, games[0], games[1]).json()["token"]

    for saved in db_session.execute(select(SavedSelection)).scalars():  # sender deletes their own copy
        db_session.delete(saved)
    db_session.commit()

    got = client.get(f"/api/betcodes/shared/{token}", headers=other_member(db_session)).json()
    assert len(got["games"]) == 2


def test_an_unknown_or_expired_link_is_the_same_plain_404(db_session, auth_headers, games):
    friend = other_member(db_session)
    token = _share(auth_headers, games[0]).json()["token"]
    share = db_session.execute(select(SharedSelection)).scalar_one()
    share.expires_at = utcnow() - dt.timedelta(seconds=1)
    db_session.commit()

    expired = client.get(f"/api/betcodes/shared/{token}", headers=friend)
    unknown = client.get("/api/betcodes/shared/not-a-real-link", headers=friend)

    assert expired.status_code == unknown.status_code == 404
    assert expired.json() == unknown.json()


def test_games_that_have_started_come_back_marked_so_the_recipient_can_drop_them(db_session, auth_headers, games):
    token = _share(auth_headers, games[0], games[1]).json()["token"]
    games[1].date = utcnow() - dt.timedelta(hours=1)
    db_session.commit()

    got = client.get(f"/api/betcodes/shared/{token}", headers=other_member(db_session)).json()

    assert [g["upcoming"] for g in got["games"]] == [True, False]
    assert got["upcoming_count"] == 1


def test_picks_that_cannot_be_shared_are_reported_and_the_rest_kept(db_session, auth_headers, games):
    games[1].status = "FINISHED"
    db_session.commit()

    created = _share(auth_headers, games[0], games[1])

    assert created.status_code == 200
    assert len(created.json()["games"]) == 1 and created.json()["warnings"]
    games[0].status = "FINISHED"
    db_session.commit()
    nothing = _share(auth_headers, games[0])
    assert nothing.status_code == 422


def test_the_same_pick_twice_is_shared_once_and_names_are_tidied(auth_headers, games):
    body = _share(auth_headers, games[0], games[0], name="  my   slip  ").json()
    assert len(body["games"]) == 1 and body["name"] == "my slip"
    assert _share(auth_headers, games[1], name="   ").json()["name"].startswith("Saved ")


def test_there_is_a_limit_on_active_share_links(db_session, auth_headers, games):
    owner = db_session.execute(select(User).where(User.email == "test-user@example.com")).scalar_one()
    for i in range(MAX_ACTIVE_SHARES):
        db_session.add(SharedSelection(
            token=f"tok{i:03d}", owner_user_id=owner.id, name="x", picks=picks(games[0]),
            expires_at=utcnow() + dt.timedelta(days=1),
        ))
    db_session.commit()
    full = _share(auth_headers, games[0])
    assert full.status_code == 400 and str(MAX_ACTIVE_SHARES) in full.json()["detail"]

    # An expired one stops counting.
    expired = db_session.execute(select(SharedSelection).where(SharedSelection.token == "tok000")).scalar_one()
    expired.expires_at = utcnow() - dt.timedelta(days=1)
    db_session.commit()
    assert _share(auth_headers, games[0]).status_code == 200


def test_sharing_is_recorded_without_the_picks(db_session, auth_headers, games):
    _share(auth_headers, games[0], games[1])
    entry = db_session.execute(select(AuditLog).where(AuditLog.action == "betcode.shared")).scalar_one()
    assert entry.detail == {"games": 2}


def test_deleting_the_sender_removes_their_share_links_on_a_database_that_enforces_keys(db_session, auth_headers, games):
    from app.auth.passwords import hash_password
    from app.auth.tokens import create_token
    from app.config import get_settings

    _share(auth_headers, games[0])
    owner = db_session.execute(select(User).where(User.email == "test-user@example.com")).scalar_one()
    owner_id = owner.id
    admin = User(email="admin-share@example.com", name="Admin", password_hash=hash_password("a-good-password"),
                 role="superadmin", status="active")
    db_session.add(admin)
    db_session.commit()
    db_session.refresh(admin)
    settings = get_settings()
    headers = {"Authorization": "Bearer " + create_token({"user_id": admin.id, "role": admin.role}, settings.secret_key, settings.session_ttl_seconds)}

    sqlite_foreign_keys(db_session, True)
    try:
        response = client.delete(f"/api/admin/users/{owner_id}", headers=headers)
        assert response.status_code == 200, response.text
        db_session.expire_all()
        assert db_session.execute(select(SharedSelection)).first() is None
    finally:
        sqlite_foreign_keys(db_session, False)
