"""Guda Picks: a Super Admin promotes a real outcome from a match's own
Markets tab onto every Dashboard. The design constraint that matters here is
that a pick is a *reference* (match + market + selection), never a copied
number -- so featuring it validates against the match's actual current
outcomes, and reading it back always recomputes the probability from the
match's latest Prediction rather than trusting a frozen snapshot.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app.auth.passwords import hash_password
from app.auth.tokens import create_token
from app.config import get_settings
from app.db.models import AuditLog, FeaturedPick, Match, Prediction, Team, User
from app.main import app
from tests.conftest import grant_active_access

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
    return _make_user(db_session, "picks-admin@example.com", role="superadmin")


def _prediction(match_id: int, *, home=0.55, draw=0.25, away=0.20, btts_yes=0.58) -> Prediction:
    return Prediction(
        match_id=match_id,
        model_version="test",
        home_win=home,
        draw=draw,
        away_win=away,
        over_probabilities={"1.5": 0.78, "2.5": 0.52, "3.5": 0.28},
        btts_yes=btts_yes,
        btts_no=1 - btts_yes,
        correct_score_probabilities={"2-1": 0.11, "1-1": 0.09, "1-0": 0.08},
        most_likely_score="2-1",
        most_likely_score_probability=0.11,
        global_outcome_market="Total Goals 1.5",
        global_outcome_selection="Over 1.5",
        global_outcome_probability=0.78,
        confidence="HIGH",
        data_quality_score=1.0,
        model_agreement_score=0.9,
        explanation={"positive": [], "negative": []},
        model_breakdown={},
    )


@pytest.fixture()
def upcoming_match(db_session):
    home = Team(name="Guda Home", league="League One", aliases=[])
    away = Team(name="Guda Away", league="League One", aliases=[])
    db_session.add_all([home, away])
    db_session.commit()
    for t in (home, away):
        db_session.refresh(t)

    match = Match(
        league="League One", season="2324", date=dt.datetime.utcnow() + dt.timedelta(hours=6),
        home_team_id=home.id, away_team_id=away.id, status="SCHEDULED",
    )
    db_session.add(match)
    db_session.commit()
    db_session.refresh(match)

    db_session.add(_prediction(match.id))
    db_session.commit()
    return match


def _feature(admin: User, match_id: int, market="Both Teams To Score", selection="Yes", note=None) -> dict:
    payload = {"match_id": match_id, "market": market, "selection": selection}
    if note is not None:
        payload["note"] = note
    response = client.post("/api/admin/featured-picks", json=payload, headers=_headers(admin))
    assert response.status_code == 200, response.text
    return response.json()


# --- Creation -----------------------------------------------------------


def test_admin_can_feature_a_real_outcome(db_session, admin, upcoming_match):
    body = _feature(admin, upcoming_match.id)
    assert body["market"] == "Both Teams To Score"
    assert body["selection"] == "Yes"
    assert body["probability"] == pytest.approx(0.58)
    assert body["match"]["id"] == upcoming_match.id


def test_featuring_a_nonexistent_outcome_is_rejected(db_session, admin, upcoming_match):
    response = client.post(
        "/api/admin/featured-picks",
        json={"match_id": upcoming_match.id, "market": "Match Result", "selection": "Nobody Wins"},
        headers=_headers(admin),
    )
    assert response.status_code == 400


def test_featuring_the_same_outcome_twice_is_rejected(db_session, admin, upcoming_match):
    _feature(admin, upcoming_match.id)
    response = client.post(
        "/api/admin/featured-picks",
        json={"match_id": upcoming_match.id, "market": "Both Teams To Score", "selection": "Yes"},
        headers=_headers(admin),
    )
    assert response.status_code == 400


def test_featuring_a_match_with_no_prediction_is_rejected(db_session, admin):
    home = Team(name="No Pred Home", league="League One", aliases=[])
    away = Team(name="No Pred Away", league="League One", aliases=[])
    db_session.add_all([home, away])
    db_session.commit()
    for t in (home, away):
        db_session.refresh(t)
    match = Match(league="League One", season="2324", date=dt.datetime.utcnow(), home_team_id=home.id, away_team_id=away.id, status="SCHEDULED")
    db_session.add(match)
    db_session.commit()
    db_session.refresh(match)

    response = client.post(
        "/api/admin/featured-picks",
        json={"match_id": match.id, "market": "Match Result", "selection": "Home Win"},
        headers=_headers(admin),
    )
    assert response.status_code == 400


def test_creating_a_featured_pick_is_superadmin_only(db_session, upcoming_match):
    plain = _make_user(db_session, "plain-picks@example.com")
    response = client.post(
        "/api/admin/featured-picks",
        json={"match_id": upcoming_match.id, "market": "Both Teams To Score", "selection": "Yes"},
        headers=_headers(plain),
    )
    assert response.status_code == 403


def test_creating_a_featured_pick_is_audit_logged(db_session, admin, upcoming_match):
    _feature(admin, upcoming_match.id)
    entry = db_session.query(AuditLog).filter(AuditLog.action == "featured_pick.created").order_by(AuditLog.id.desc()).first()
    assert entry is not None
    assert entry.actor_user_id == admin.id
    assert entry.detail["match_id"] == upcoming_match.id


# --- Admin list / remove -------------------------------------------------


def test_admin_can_list_and_remove_a_featured_pick(db_session, admin, upcoming_match):
    created = _feature(admin, upcoming_match.id)

    listed = client.get("/api/admin/featured-picks", headers=_headers(admin)).json()
    assert any(p["id"] == created["id"] for p in listed)

    response = client.delete(f"/api/admin/featured-picks/{created['id']}", headers=_headers(admin))
    assert response.status_code == 204

    listed_after = client.get("/api/admin/featured-picks", headers=_headers(admin)).json()
    assert not any(p["id"] == created["id"] for p in listed_after)


def test_removing_a_featured_pick_is_superadmin_only(db_session, admin, upcoming_match):
    created = _feature(admin, upcoming_match.id)
    plain = _make_user(db_session, "plain-remove@example.com")
    assert client.delete(f"/api/admin/featured-picks/{created['id']}", headers=_headers(plain)).status_code == 403


# --- Public guda-picks feed -----------------------------------------------


def test_guda_picks_shows_the_current_probability_not_a_snapshot(db_session, admin, upcoming_match, auth_headers):
    _feature(admin, upcoming_match.id)

    first = client.get("/api/predictions/guda-picks", headers=auth_headers).json()
    assert len(first) == 1
    assert first[0]["probability"] == pytest.approx(0.58)

    # The model recomputes and stores a fresh prediction -- the pick is a
    # reference, so it must follow the new number, not the one at creation.
    pred = db_session.query(Prediction).filter(Prediction.match_id == upcoming_match.id).order_by(Prediction.created_at.desc()).first()
    pred.btts_yes = 0.81
    pred.btts_no = 0.19
    db_session.commit()

    second = client.get("/api/predictions/guda-picks", headers=auth_headers).json()
    assert second[0]["probability"] == pytest.approx(0.81)


def test_guda_picks_requires_login(db_session, admin, upcoming_match):
    _feature(admin, upcoming_match.id)
    assert client.get("/api/predictions/guda-picks").status_code == 401


def test_guda_picks_master_toggle_hides_it_from_everyone(db_session, admin, upcoming_match, auth_headers):
    _feature(admin, upcoming_match.id)
    client.patch("/api/admin/settings", json={"values": {"guda_picks_enabled": False}}, headers=_headers(admin))

    assert client.get("/api/predictions/guda-picks", headers=auth_headers).json() == []
    assert client.get("/api/predictions/guda-picks", headers=_headers(admin)).json() == []


def test_guda_picks_can_be_restricted_to_accounts_with_access(db_session, admin, upcoming_match, headers_no_access):
    _feature(admin, upcoming_match.id)
    client.patch("/api/admin/settings", json={"values": {"guda_picks_free_tier_visible": False}}, headers=_headers(admin))

    assert client.get("/api/predictions/guda-picks", headers=headers_no_access).json() == []


def test_guda_picks_still_shows_to_accounts_with_access_when_free_tier_is_restricted(db_session, admin, upcoming_match):
    _feature(admin, upcoming_match.id)
    client.patch("/api/admin/settings", json={"values": {"guda_picks_free_tier_visible": False}}, headers=_headers(admin))

    premium = _make_user(db_session, "premium-picks@example.com")
    grant_active_access(db_session, premium)
    body = client.get("/api/predictions/guda-picks", headers=_headers(premium)).json()
    assert len(body) == 1


def test_an_expired_pick_is_not_returned(db_session, admin, upcoming_match, auth_headers):
    created = _feature(admin, upcoming_match.id)
    pick = db_session.get(FeaturedPick, created["id"])
    pick.expires_at = dt.datetime.utcnow() - dt.timedelta(minutes=1)
    db_session.commit()

    assert client.get("/api/predictions/guda-picks", headers=auth_headers).json() == []


def test_a_pick_drops_out_once_its_match_has_no_prediction(db_session, admin, upcoming_match, auth_headers):
    _feature(admin, upcoming_match.id)
    db_session.query(Prediction).filter(Prediction.match_id == upcoming_match.id).delete()
    db_session.commit()

    assert client.get("/api/predictions/guda-picks", headers=auth_headers).json() == []
