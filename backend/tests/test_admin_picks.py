"""Admin Picks: a Super Admin promotes a whole multi-leg AI Generation slip
onto every Dashboard, distinct from Guda Picks' single-outcome promotion
(see test_guda_picks.py). Same design constraint, applied per leg: a pick
is a *reference* to each (match, market, selection), never a copied number
-- so creating one re-prices every leg from scratch, and reading it back
always recomputes the combined odds/probability/risk tier live rather than
trusting a frozen snapshot.
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
    return _make_user(db_session, "admin-picks-admin@example.com", role="superadmin")


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
        global_outcome_market="Match Result",
        global_outcome_selection="Home Win",
        global_outcome_probability=home,
        confidence="HIGH",
        data_quality_score=1.0,
        model_agreement_score=0.9,
        explanation={"positive": [], "negative": []},
        model_breakdown={},
    )


def _match_with_odds(db, name_prefix: str, league="League One", **prediction_kwargs) -> Match:
    home = Team(name=f"{name_prefix} Home", league=league, aliases=[])
    away = Team(name=f"{name_prefix} Away", league=league, aliases=[])
    db.add_all([home, away])
    db.commit()
    for t in (home, away):
        db.refresh(t)

    match = Match(
        league=league, season="2324", date=dt.datetime.utcnow() + dt.timedelta(hours=6),
        home_team_id=home.id, away_team_id=away.id, status="SCHEDULED",
    )
    db.add(match)
    db.commit()
    db.refresh(match)

    db.add(_prediction(match.id, **prediction_kwargs))
    db.add(MatchOdds(match_id=match.id, bookmaker="Bet365", market="Match Result", selection="Home Win", decimal_odds=1.80))
    db.commit()
    return match


@pytest.fixture()
def two_matches(db_session):
    return [
        _match_with_odds(db_session, "Alpha", home=0.60),
        _match_with_odds(db_session, "Bravo", home=0.55),
    ]


def _create(admin: User, legs: list[dict], label=None, note=None) -> dict:
    payload = {"legs": legs}
    if label is not None:
        payload["label"] = label
    if note is not None:
        payload["note"] = note
    response = client.post("/api/admin/admin-picks", json=payload, headers=_headers(admin))
    assert response.status_code == 200, response.text
    return response.json()


def _legs(matches: list[Match]) -> list[dict]:
    return [{"match_id": m.id, "market": "Match Result", "selection": "Home Win"} for m in matches]


# --- Creation -------------------------------------------------------------


def test_admin_can_feature_a_multi_leg_slip(db_session, admin, two_matches):
    body = _create(admin, _legs(two_matches), label="Weekend Banker", note="Two safe favorites")
    assert len(body["legs"]) == 2
    assert body["label"] == "Weekend Banker"
    assert body["note"] == "Two safe favorites"
    assert body["combined_odds"] == pytest.approx(1.80 * 1.80)
    assert body["combined_probability"] == pytest.approx(0.60 * 0.55)


def test_combined_probability_above_50_percent_is_low_risk(db_session, admin, two_matches):
    # 0.60 * 0.55 = 0.33 -- medium, not low; pick legs that clear 50%.
    body = _create(admin, _legs(two_matches))
    assert body["risk_tier"] == "medium"


def test_risk_tier_is_high_for_a_long_shot_combo(db_session, admin):
    matches = [
        _match_with_odds(db_session, "Charlie", home=0.30),
        _match_with_odds(db_session, "Delta", home=0.25),
        _match_with_odds(db_session, "Echo", home=0.20),
    ]
    body = _create(admin, _legs(matches))
    # 0.30 * 0.25 * 0.20 = 0.015, well under the 20% "high" cutoff.
    assert body["risk_tier"] == "high"


def test_an_empty_slip_is_rejected(db_session, admin):
    response = client.post("/api/admin/admin-picks", json={"legs": []}, headers=_headers(admin))
    assert response.status_code == 400


def test_a_leg_with_no_stored_price_is_rejected(db_session, admin):
    home = Team(name="Unpriced Home", league="League One", aliases=[])
    away = Team(name="Unpriced Away", league="League One", aliases=[])
    db_session.add_all([home, away])
    db_session.commit()
    for t in (home, away):
        db_session.refresh(t)
    match = Match(league="League One", season="2324", date=dt.datetime.utcnow(), home_team_id=home.id, away_team_id=away.id, status="SCHEDULED")
    db_session.add(match)
    db_session.commit()
    db_session.refresh(match)
    db_session.add(_prediction(match.id))
    db_session.commit()

    response = client.post(
        "/api/admin/admin-picks",
        json={"legs": [{"match_id": match.id, "market": "Match Result", "selection": "Home Win"}]},
        headers=_headers(admin),
    )
    assert response.status_code == 400


def test_creating_an_admin_pick_is_superadmin_only(db_session, two_matches):
    plain = _make_user(db_session, "plain-admin-picks@example.com")
    response = client.post("/api/admin/admin-picks", json={"legs": _legs(two_matches)}, headers=_headers(plain))
    assert response.status_code == 403


def test_creating_an_admin_pick_is_audit_logged(db_session, admin, two_matches):
    _create(admin, _legs(two_matches))
    entry = db_session.query(AuditLog).filter(AuditLog.action == "admin_pick.created").order_by(AuditLog.id.desc()).first()
    assert entry is not None
    assert entry.actor_user_id == admin.id
    assert entry.detail["legs"] == 2


# --- Admin list / remove ---------------------------------------------------


def test_admin_can_list_and_remove_an_admin_pick(db_session, admin, two_matches):
    created = _create(admin, _legs(two_matches))

    listed = client.get("/api/admin/admin-picks", headers=_headers(admin)).json()
    assert any(p["id"] == created["id"] for p in listed)

    response = client.delete(f"/api/admin/admin-picks/{created['id']}", headers=_headers(admin))
    assert response.status_code == 204

    listed_after = client.get("/api/admin/admin-picks", headers=_headers(admin)).json()
    assert not any(p["id"] == created["id"] for p in listed_after)


def test_removing_an_admin_pick_is_superadmin_only(db_session, admin, two_matches):
    created = _create(admin, _legs(two_matches))
    plain = _make_user(db_session, "plain-remove-admin-pick@example.com")
    assert client.delete(f"/api/admin/admin-picks/{created['id']}", headers=_headers(plain)).status_code == 403


# --- Public admin-picks feed ------------------------------------------------


def test_admin_picks_recomputes_live_not_a_snapshot(db_session, admin, two_matches, auth_headers):
    _create(admin, _legs(two_matches))

    first = client.get("/api/predictions/admin-picks", headers=auth_headers).json()
    assert len(first) == 1
    assert first[0]["combined_probability"] == pytest.approx(0.60 * 0.55)

    pred = db_session.query(Prediction).filter(Prediction.match_id == two_matches[0].id).order_by(Prediction.created_at.desc()).first()
    pred.home_win = 0.90
    pred.global_outcome_probability = 0.90
    db_session.commit()

    second = client.get("/api/predictions/admin-picks", headers=auth_headers).json()
    assert second[0]["combined_probability"] == pytest.approx(0.90 * 0.55)


def test_admin_picks_requires_login(db_session, admin, two_matches):
    _create(admin, _legs(two_matches))
    assert client.get("/api/predictions/admin-picks").status_code == 401


def test_admin_picks_master_toggle_hides_it_from_everyone(db_session, admin, two_matches, auth_headers):
    _create(admin, _legs(two_matches))
    client.patch("/api/admin/settings", json={"values": {"admin_picks_enabled": False}}, headers=_headers(admin))

    assert client.get("/api/predictions/admin-picks", headers=auth_headers).json() == []
    assert client.get("/api/predictions/admin-picks", headers=_headers(admin)).json() == []


def test_admin_picks_can_be_restricted_to_accounts_with_access(db_session, admin, two_matches, headers_no_access):
    _create(admin, _legs(two_matches))
    client.patch("/api/admin/settings", json={"values": {"admin_picks_free_tier_visible": False}}, headers=_headers(admin))

    assert client.get("/api/predictions/admin-picks", headers=headers_no_access).json() == []


def test_admin_picks_still_shows_to_accounts_with_access_when_free_tier_is_restricted(db_session, admin, two_matches):
    _create(admin, _legs(two_matches))
    client.patch("/api/admin/settings", json={"values": {"admin_picks_free_tier_visible": False}}, headers=_headers(admin))

    premium = _make_user(db_session, "premium-admin-picks@example.com")
    grant_active_access(db_session, premium)
    body = client.get("/api/predictions/admin-picks", headers=_headers(premium)).json()
    assert len(body) == 1


def test_an_expired_admin_pick_is_not_returned(db_session, admin, two_matches, auth_headers):
    created = _create(admin, _legs(two_matches))
    pick = db_session.get(AdminPick, created["id"])
    pick.expires_at = dt.datetime.utcnow() - dt.timedelta(minutes=1)
    db_session.commit()

    assert client.get("/api/predictions/admin-picks", headers=auth_headers).json() == []


def test_a_slip_drops_out_entirely_once_one_leg_stops_resolving(db_session, admin, two_matches, auth_headers):
    """A combo is only as good as every leg in it -- losing one leg's price
    must drop the whole slip, not silently show a shorter one that no
    longer matches what was actually promoted."""

    _create(admin, _legs(two_matches))
    db_session.query(MatchOdds).filter(MatchOdds.match_id == two_matches[0].id).delete()
    db_session.commit()

    assert client.get("/api/predictions/admin-picks", headers=auth_headers).json() == []
    # And the admin management view drops it too, not just the public feed.
    assert client.get("/api/admin/admin-picks", headers=_headers(admin)).json() == []
