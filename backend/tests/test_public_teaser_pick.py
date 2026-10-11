"""Public teaser pick: the one showcase combo shown with real selections on
the public, unauthenticated Welcome page -- a deliberate, narrow exception to
this platform's usual "selections stay behind the login" rule. Reuses the
AdminPick table (AdminPick.public_teaser) rather than a separate one, but is
excluded from every member/admin-facing Admin Picks listing and never
settles -- it's always a live, upcoming combo, replaced wholesale by the
next randomize call rather than accumulating history.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app.auth.passwords import hash_password
from app.auth.tokens import create_token
from app.config import get_settings
from app.db.models import AdminPick, AuditLog, Match, MatchOdds, Prediction, Team, User
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
    return _make_user(db_session, "teaser-admin@example.com", role="superadmin")


def _prediction(match_id: int, *, home=0.70) -> Prediction:
    return Prediction(
        match_id=match_id,
        model_version="test",
        home_win=home,
        draw=(1 - home) / 2,
        away_win=(1 - home) / 2,
        over_probabilities={"1.5": 0.78, "2.5": 0.52, "3.5": 0.28},
        btts_yes=0.5,
        btts_no=0.5,
        correct_score_probabilities={"2-1": 0.11, "1-1": 0.09, "1-0": 0.08},
        most_likely_score="2-1",
        most_likely_score_probability=0.11,
        global_outcome_market="Match Result",
        global_outcome_selection="Home Win",
        global_outcome_probability=home,
        confidence="HIGH",
        data_quality_score=1.0,
        model_agreement_score=0.9,
        explanation={"positive": [], "negative": []},
        model_breakdown={},
    )


def _scheduled_match_with_odds(db, name_prefix: str, *, home=0.70, decimal_odds=1.90) -> Match:
    """A real, priced, upcoming candidate -- the exact shape
    select_random_priced_legs's build_candidate_legs looks for: SCHEDULED,
    within the default days-ahead window, a Prediction clearing the 65%
    accuracy floor, and a real MatchOdds row."""

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

    db.add(_prediction(match.id, home=home))
    db.add(MatchOdds(match_id=match.id, bookmaker="Bet365", market="Match Result", selection="Home Win", decimal_odds=decimal_odds))
    db.commit()
    return match


@pytest.fixture()
def priced_matches(db_session):
    """Four independent 1.90-odds candidates -- any three combine to
    ~6.86x, comfortably clearing a 5.0 target regardless of which three a
    random shuffle happens to pick."""

    return [_scheduled_match_with_odds(db_session, name) for name in ("Alpha", "Bravo", "Charlie", "Delta")]


def _randomize(admin: User, min_odds: float = 5.0):
    return client.post("/api/admin/public-teaser-pick/randomize", json={"min_odds": min_odds}, headers=_headers(admin))


def test_public_teaser_pick_has_no_data_when_none_set(db_session):
    response = client.get("/api/public/teaser-pick")
    assert response.status_code == 200
    assert response.json() == {
        "has_data": False, "legs": [], "combined_odds": 0.0, "combined_probability": 0.0,
        "label": None, "created_at": None,
    }


def test_admin_can_randomize_a_public_teaser_pick(db_session, admin, priced_matches):
    response = _randomize(admin, min_odds=5.0)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["has_data"] is True
    assert body["combined_odds"] >= 5.0
    assert len(body["legs"]) >= 1
    for leg in body["legs"]:
        assert leg["decimal_odds"] is not None
        assert leg["result"] is None  # never settled

    # The public, unauthenticated endpoint shows the same thing.
    public = client.get("/api/public/teaser-pick")
    assert public.status_code == 200
    assert public.json()["has_data"] is True
    assert public.json()["combined_odds"] == pytest.approx(body["combined_odds"])


def test_randomize_is_superadmin_only(db_session, priced_matches):
    user = _make_user(db_session, "not-an-admin@example.com")
    response = _randomize(user, min_odds=5.0)
    assert response.status_code == 403


def test_randomize_rejects_min_odds_of_one_or_less(db_session, admin, priced_matches):
    response = _randomize(admin, min_odds=1.0)
    assert response.status_code == 400


def test_randomize_rejects_when_target_is_unreachable(db_session, admin):
    # No matches at all -- nothing qualifies as a candidate.
    response = _randomize(admin, min_odds=5.0)
    assert response.status_code == 400


def test_randomizing_again_replaces_the_previous_teaser(db_session, admin, priced_matches):
    first = _randomize(admin, min_odds=5.0)
    assert first.status_code == 200, first.text
    second = _randomize(admin, min_odds=5.0)
    assert second.status_code == 200, second.text

    rows = db_session.query(AdminPick).filter(AdminPick.public_teaser.is_(True)).all()
    assert len(rows) == 1

    logs = db_session.query(AuditLog).filter(AuditLog.action == "public_teaser_pick.randomized").all()
    assert len(logs) == 2


def test_admin_can_clear_the_public_teaser_pick(db_session, admin, priced_matches):
    _randomize(admin, min_odds=5.0)
    response = client.delete("/api/admin/public-teaser-pick", headers=_headers(admin))
    assert response.status_code == 204

    public = client.get("/api/public/teaser-pick")
    assert public.json()["has_data"] is False

    log = db_session.query(AuditLog).filter(AuditLog.action == "public_teaser_pick.cleared").one()
    assert log.detail == {"removed": 1}


def test_clearing_when_nothing_is_set_is_a_quiet_no_op(db_session, admin):
    response = client.delete("/api/admin/public-teaser-pick", headers=_headers(admin))
    assert response.status_code == 204
    assert db_session.query(AuditLog).filter(AuditLog.action == "public_teaser_pick.cleared").count() == 0


def test_clearing_is_superadmin_only(db_session, priced_matches, admin):
    _randomize(admin, min_odds=5.0)
    user = _make_user(db_session, "not-an-admin-2@example.com")
    response = client.delete("/api/admin/public-teaser-pick", headers=_headers(user))
    assert response.status_code == 403


def test_public_teaser_pick_is_excluded_from_the_dashboard_admin_picks(db_session, admin, priced_matches):
    _randomize(admin, min_odds=5.0)
    response = client.get("/api/predictions/admin-picks", headers=_headers(admin))
    assert response.status_code == 200
    assert all(p["label"] != "Public teaser pick" for p in response.json())


def test_public_teaser_pick_is_excluded_from_the_admin_management_list(db_session, admin, priced_matches):
    _randomize(admin, min_odds=5.0)
    response = client.get("/api/admin/admin-picks", headers=_headers(admin))
    assert response.status_code == 200
    assert all(p["label"] != "Public teaser pick" for p in response.json())


def test_public_teaser_pick_never_counts_toward_the_public_track_record(db_session, admin, priced_matches):
    _randomize(admin, min_odds=5.0)
    response = client.get("/api/public/admin-picks-track-record")
    assert response.json()["has_data"] is False


def test_admin_preview_matches_the_public_endpoint(db_session, admin, priced_matches):
    randomized = _randomize(admin, min_odds=5.0).json()
    preview = client.get("/api/admin/public-teaser-pick", headers=_headers(admin))
    assert preview.status_code == 200
    assert preview.json()["combined_odds"] == pytest.approx(randomized["combined_odds"])


def test_a_teaser_past_its_first_legs_kickoff_goes_stale(db_session, admin, priced_matches):
    _randomize(admin, min_odds=5.0)
    pick = db_session.query(AdminPick).filter(AdminPick.public_teaser.is_(True)).one()
    pick.expires_at = dt.datetime.utcnow() - dt.timedelta(minutes=1)
    db_session.commit()

    response = client.get("/api/public/teaser-pick")
    assert response.json()["has_data"] is False
