"""Committing a booking code to the public track record: an admin's own
"My codes" (BookingSlip) is personal, but one whose matches haven't kicked
off can be promoted into a new, pending, featured_public AdminPick that
then settles on its own -- win or lose, it stays (see
routes_admin.promote_booking_slip_to_track_record and app.track_record).
The source BookingSlip itself is never touched.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app.auth.passwords import hash_password
from app.auth.tokens import create_token
from app.config import get_settings
from app.db.models import AdminPick, AuditLog, BookingSlip, Match, Prediction, Team, User
from app.main import app

client = TestClient(app)


def _headers(user: User) -> dict:
    settings = get_settings()
    token = create_token({"user_id": user.id, "role": user.role}, settings.secret_key, settings.session_ttl_seconds)
    return {"Authorization": f"Bearer {token}"}


def _make_user(db, email: str, role: str = "user") -> User:
    user = User(email=email, name=email.split("@")[0], password_hash=hash_password("a-good-password"), role=role, status="active")
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture()
def admin(db_session):
    return _make_user(db_session, "track-record-admin@example.com", role="superadmin")


def _prediction(match_id: int, *, home=0.70) -> Prediction:
    return Prediction(
        match_id=match_id, model_version="test", home_win=home, draw=(1 - home) / 2, away_win=(1 - home) / 2,
        over_probabilities={"1.5": 0.78, "2.5": 0.52, "3.5": 0.28}, btts_yes=0.5, btts_no=0.5,
        correct_score_probabilities={"2-1": 0.11, "1-1": 0.09, "1-0": 0.08}, most_likely_score="2-1",
        most_likely_score_probability=0.11, global_outcome_market="Match Result", global_outcome_selection="Home Win",
        global_outcome_probability=home, confidence="HIGH", data_quality_score=1.0, model_agreement_score=0.9,
        explanation={"positive": [], "negative": []}, model_breakdown={},
    )


def _scheduled_match(db, name_prefix: str) -> Match:
    home_team = Team(name=f"{name_prefix} Home", league="League One", aliases=[])
    away_team = Team(name=f"{name_prefix} Away", league="League One", aliases=[])
    db.add_all([home_team, away_team])
    db.commit()
    for t in (home_team, away_team):
        db.refresh(t)

    match = Match(
        league="League One", season="2324", date=dt.datetime.utcnow() + dt.timedelta(hours=6),
        home_team_id=home_team.id, away_team_id=away_team.id, status="SCHEDULED",
    )
    db.add(match)
    db.commit()
    db.refresh(match)
    db.add(_prediction(match.id))
    db.commit()
    return match


@pytest.fixture()
def two_matches(db_session):
    return [_scheduled_match(db_session, "Alpha"), _scheduled_match(db_session, "Bravo")]


def _leg_json(match: Match) -> dict:
    """Mirrors Leg.as_json() -- the exact shape BookingSlip.legs stores,
    unpriced (decimal_odds None) since book_picks never attaches a
    bookmaker quote per leg -- each site prices its own slip when opened."""

    return {
        "match_id": match.id,
        "league": match.league,
        "home_team": match.home_team.name,
        "away_team": match.away_team.name,
        "kickoff": match.date.isoformat(),
        "market": "Match Result",
        "selection": "Home Win",
        "model_probability": 0.70,
        "decimal_odds": None,
        "priced_by": None,
    }


def _make_slip(db, user: User, matches: list[Match]) -> BookingSlip:
    slip = BookingSlip(
        user_id=user.id,
        bookmaker="SportyBet",
        criteria={"picks": [{"match_id": m.id, "market": "Match Result", "selection": "Home Win"} for m in matches], "sites": ["sportybet_gh"]},
        legs=[_leg_json(m) for m in matches],
        combined_odds=None,
        combined_probability=0.49,
        expires_at=max(m.date for m in matches) + dt.timedelta(days=2),
        provider="direct",
        status="code_ready",
        booking_code="REC001",
        site_codes=[{"name": "SportyBet", "status": "code_ready", "code": "REC001", "link": None, "message": None}],
    )
    db.add(slip)
    db.commit()
    db.refresh(slip)
    return slip


def _finish(db, match: Match, home_score: int, away_score: int) -> None:
    match.status = "FINISHED"
    match.home_score = home_score
    match.away_score = away_score
    db.commit()


def _promote(admin: User, slip_id: int):
    return client.post(f"/api/admin/my-booking-slips/{slip_id}/promote-to-track-record", headers=_headers(admin))


def _feature_public(admin: User, pick_id: int) -> dict:
    response = client.patch(
        f"/api/admin/admin-picks/{pick_id}/featured-public", json={"featured_public": True}, headers=_headers(admin)
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_admin_can_commit_an_upcoming_booking_slip_and_it_settles_on_its_own(db_session, admin, two_matches):
    slip = _make_slip(db_session, admin, two_matches)

    response = _promote(admin, slip.id)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["result"] == "pending"
    assert body["featured_public"] is True
    assert body["priced"] is False

    pick = db_session.get(AdminPick, body["id"])
    assert pick.featured_public_at is not None

    for match in two_matches:
        _finish(db_session, match, 2, 0)  # Home Win, as every leg picked

    data = client.get("/api/public/admin-picks-track-record").json()
    assert data["has_data"] is True
    assert data["won"] == 1
    assert data["settled_count"] == 1

    # The source BookingSlip is untouched.
    db_session.refresh(slip)
    assert db_session.get(BookingSlip, slip.id) is not None

    log = db_session.query(AuditLog).filter(AuditLog.action == "booking_slip.promoted_to_track_record").one()
    assert log.detail == {"booking_slip_id": slip.id, "legs": 2}


def test_a_committed_slip_that_loses_is_shown_as_lost(db_session, admin, two_matches):
    slip = _make_slip(db_session, admin, two_matches)
    assert _promote(admin, slip.id).status_code == 200
    for match in two_matches:
        _finish(db_session, match, 0, 2)  # every "Home Win" leg loses

    assert client.get("/api/public/admin-picks-track-record").json()["lost"] == 1


def test_promote_rejects_a_slip_whose_result_is_already_known(db_session, admin, two_matches):
    slip = _make_slip(db_session, admin, two_matches)
    for match in two_matches:
        _finish(db_session, match, 2, 0)

    response = _promote(admin, slip.id)
    assert response.status_code == 400
    assert db_session.query(AdminPick).count() == 0


def test_promote_rejects_a_slip_once_its_first_match_has_kicked_off(db_session, admin, two_matches):
    two_matches[0].date = dt.datetime.utcnow() - dt.timedelta(minutes=5)
    db_session.commit()
    slip = _make_slip(db_session, admin, two_matches)

    assert _promote(admin, slip.id).status_code == 400


def test_promote_rejects_someone_elses_booking_slip(db_session, admin, two_matches):
    other = _make_user(db_session, "other-member@example.com")
    slip = _make_slip(db_session, other, two_matches)

    response = _promote(admin, slip.id)
    assert response.status_code == 404
    assert db_session.query(AdminPick).count() == 0


def test_promote_rejects_an_unknown_slip_id(db_session, admin):
    response = _promote(admin, 999999)
    assert response.status_code == 404


def test_promote_is_superadmin_only(db_session, admin, two_matches):
    slip = _make_slip(db_session, admin, two_matches)

    plain = _make_user(db_session, "plain-user@example.com")
    response = client.post(
        f"/api/admin/my-booking-slips/{slip.id}/promote-to-track-record", headers=_headers(plain)
    )
    assert response.status_code == 403


def test_the_same_slip_cannot_be_committed_twice(db_session, admin, two_matches):
    """A duplicate would count one call twice in the public record."""

    slip = _make_slip(db_session, admin, two_matches)
    assert _promote(admin, slip.id).status_code == 200
    second = _promote(admin, slip.id)
    assert second.status_code == 409
    assert db_session.query(AdminPick).filter(AdminPick.featured_public.is_(True)).count() == 1
