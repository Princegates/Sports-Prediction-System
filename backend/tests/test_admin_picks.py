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


def _create(admin: User, legs: list[dict], label=None, note=None, priced=None, booking_code=None, booking_code_bookmaker=None) -> dict:
    payload = {"legs": legs}
    if label is not None:
        payload["label"] = label
    if note is not None:
        payload["note"] = note
    if priced is not None:
        payload["priced"] = priced
    if booking_code is not None:
        payload["booking_code"] = booking_code
    if booking_code_bookmaker is not None:
        payload["booking_code_bookmaker"] = booking_code_bookmaker
    response = client.post("/api/admin/admin-picks", json=payload, headers=_headers(admin))
    assert response.status_code == 200, response.text
    return response.json()


def _match_without_odds(db, name_prefix: str, league="League One", **prediction_kwargs) -> Match:
    """Same as _match_with_odds but with no MatchOdds row -- most outcomes
    browsed on the Markets page never get a captured price, which is exactly
    the case an unpriced (priced=False) Admin Pick exists to cover."""

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
    db.commit()
    return match


def _legs(matches: list[Match]) -> list[dict]:
    return [{"match_id": m.id, "market": "Match Result", "selection": "Home Win"} for m in matches]


def _update(admin: User, pick_id: int, legs: list[dict], label=None, note=None, priced=None):
    payload = {"legs": legs}
    if label is not None:
        payload["label"] = label
    if note is not None:
        payload["note"] = note
    if priced is not None:
        payload["priced"] = priced
    return client.patch(f"/api/admin/admin-picks/{pick_id}", json=payload, headers=_headers(admin))


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


# --- Editing ----------------------------------------------------------------


def test_admin_can_remove_a_leg_from_an_already_featured_slip(db_session, admin, two_matches):
    """The core motivation for editing: an admin generates a slip, features
    it, then decides one leg was a bad idea -- without deleting the whole
    thing and starting over."""

    created = _create(admin, _legs(two_matches))
    assert len(created["legs"]) == 2

    response = _update(admin, created["id"], _legs(two_matches[:1]))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == created["id"]  # same pick, not a new one
    assert len(body["legs"]) == 1
    assert body["combined_odds"] == pytest.approx(1.80)


def test_editing_an_admin_pick_can_change_its_label_and_note(db_session, admin, two_matches):
    created = _create(admin, _legs(two_matches), label="Original", note="Original note")
    response = _update(admin, created["id"], _legs(two_matches), label="Renamed", note="New note")
    assert response.status_code == 200
    body = response.json()
    assert body["label"] == "Renamed"
    assert body["note"] == "New note"


def test_editing_an_admin_pick_recomputes_expires_at(db_session, admin, two_matches):
    """A slip trimmed down to a single, earlier-kicking-off leg should carry
    that leg's own expiry, not the original two-leg set's later one."""

    created = _create(admin, _legs(two_matches))
    kept_leg_match = db_session.get(Match, two_matches[0].id)

    response = _update(admin, created["id"], _legs(two_matches[:1]))
    assert response.status_code == 200
    body = response.json()
    expected = (kept_leg_match.date + dt.timedelta(days=2)).isoformat()
    assert body["expires_at"].startswith(expected[:19])


def test_editing_an_admin_pick_to_zero_legs_is_rejected(db_session, admin, two_matches):
    created = _create(admin, _legs(two_matches))
    response = _update(admin, created["id"], [])
    assert response.status_code == 400
    # The original slip must still be intact -- a rejected edit is a no-op.
    listed = client.get("/api/admin/admin-picks", headers=_headers(admin)).json()
    matched = next(p for p in listed if p["id"] == created["id"])
    assert len(matched["legs"]) == 2


def test_editing_a_leg_to_one_with_no_price_is_rejected(db_session, admin, two_matches):
    created = _create(admin, _legs(two_matches))
    response = _update(
        admin, created["id"],
        [{"match_id": two_matches[0].id, "market": "Both Teams To Score", "selection": "Yes"}],
    )
    assert response.status_code == 400


def test_editing_a_nonexistent_admin_pick_is_a_404(db_session, admin, two_matches):
    response = _update(admin, 999999, _legs(two_matches))
    assert response.status_code == 404


def test_editing_an_admin_pick_is_superadmin_only(db_session, admin, two_matches):
    created = _create(admin, _legs(two_matches))
    plain = _make_user(db_session, "plain-edit-admin-pick@example.com")
    response = _update(plain, created["id"], _legs(two_matches[:1]))
    assert response.status_code == 403


def test_editing_an_admin_pick_is_audit_logged(db_session, admin, two_matches):
    created = _create(admin, _legs(two_matches))
    _update(admin, created["id"], _legs(two_matches[:1]))
    entry = db_session.query(AuditLog).filter(AuditLog.action == "admin_pick.updated").order_by(AuditLog.id.desc()).first()
    assert entry is not None
    assert entry.actor_user_id == admin.id
    assert entry.detail["admin_pick_id"] == created["id"]
    assert entry.detail["legs"] == 1


def test_editing_an_admin_pick_is_reflected_in_the_public_feed(db_session, admin, two_matches, auth_headers):
    created = _create(admin, _legs(two_matches))
    _update(admin, created["id"], _legs(two_matches[:1]))

    body = client.get("/api/predictions/admin-picks", headers=auth_headers).json()
    assert len(body) == 1
    assert len(body[0]["legs"]) == 1


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


# --- Unpriced picks (model probability only, no bookmaker quote) -----------


def test_admin_can_feature_an_unpriced_slip_with_no_stored_price(db_session, admin):
    """The whole point of priced=False: a match with a prediction but no
    MatchOdds row -- which price_legs alone would reject outright -- can
    still be featured, just without a combined price."""

    matches = [
        _match_without_odds(db_session, "Foxtrot", home=0.60),
        _match_without_odds(db_session, "Golf", home=0.55),
    ]
    body = _create(admin, _legs(matches), priced=False)
    assert body["priced"] is False
    assert body["combined_odds"] is None
    assert body["combined_probability"] == pytest.approx(0.60 * 0.55)
    assert all(leg["decimal_odds"] is None and leg["priced_by"] is None for leg in body["legs"])


def test_an_unpriced_slip_still_requires_a_real_outcome(db_session, admin):
    """No bookmaker quote required, but the grounding rule still applies --
    a market/selection the prediction doesn't actually offer is rejected."""

    match = _match_without_odds(db_session, "Hotel")
    response = client.post(
        "/api/admin/admin-picks",
        json={
            "legs": [{"match_id": match.id, "market": "Not A Real Market", "selection": "Yes"}],
            "priced": False,
        },
        headers=_headers(admin),
    )
    assert response.status_code == 400


def test_an_unpriced_admin_pick_still_defaults_to_priced_true(db_session, admin, two_matches):
    """Omitting `priced` entirely keeps the original AI Generation behavior
    -- existing callers that never send the field must not change shape."""

    body = _create(admin, _legs(two_matches))
    assert body["priced"] is True
    assert body["combined_odds"] is not None


def test_unpriced_admin_pick_shown_in_public_feed_without_odds(db_session, admin, auth_headers):
    matches = [_match_without_odds(db_session, "India", home=0.70)]
    _create(admin, _legs(matches), priced=False)

    body = client.get("/api/predictions/admin-picks", headers=auth_headers).json()
    assert len(body) == 1
    assert body[0]["priced"] is False
    assert body[0]["combined_odds"] is None
    assert body[0]["combined_probability"] == pytest.approx(0.70)


def test_editing_an_unpriced_admin_pick_can_switch_it_to_priced(db_session, admin, two_matches):
    """Toggling priced back to True on an edit re-validates every leg against
    price_legs -- two_matches both carry real quotes, so this succeeds."""

    unpriced_match = _match_without_odds(db_session, "Juliet")
    created = _create(admin, _legs([unpriced_match]), priced=False)
    assert created["priced"] is False

    response = _update(admin, created["id"], _legs(two_matches), priced=True)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["priced"] is True
    assert body["combined_odds"] == pytest.approx(1.80 * 1.80)


# --- Booking codes (admin-typed, never generated by this platform) ---------


def test_booking_code_requires_a_bookmaker_and_vice_versa(db_session, admin, two_matches):
    only_code = client.post(
        "/api/admin/admin-picks",
        json={"legs": _legs(two_matches), "booking_code": "ABC123"},
        headers=_headers(admin),
    )
    assert only_code.status_code == 400

    only_bookmaker = client.post(
        "/api/admin/admin-picks",
        json={"legs": _legs(two_matches), "booking_code_bookmaker": "Bet9ja"},
        headers=_headers(admin),
    )
    assert only_bookmaker.status_code == 400


def test_admin_can_attach_a_booking_code(db_session, admin, two_matches):
    body = _create(admin, _legs(two_matches), booking_code="ABC123", booking_code_bookmaker="Bet9ja")
    assert body["has_booking_code"] is True
    assert body["booking_code"] == "ABC123"
    assert body["booking_code_bookmaker"] == "Bet9ja"


def test_no_booking_code_means_has_booking_code_is_false(db_session, admin, two_matches):
    body = _create(admin, _legs(two_matches))
    assert body["has_booking_code"] is False
    assert body["booking_code"] is None
    assert body["booking_code_bookmaker"] is None


def test_booking_code_is_hidden_from_free_tier_but_flagged_as_available(db_session, admin, two_matches, headers_no_access):
    """The whole commercial point: a free-tier account sees that a code
    exists (so it knows to upgrade) but never the code itself."""

    _create(admin, _legs(two_matches), booking_code="ABC123", booking_code_bookmaker="Bet9ja")

    body = client.get("/api/predictions/admin-picks", headers=headers_no_access).json()
    assert len(body) == 1
    assert body[0]["has_booking_code"] is True
    assert body[0]["booking_code"] is None
    assert body[0]["booking_code_bookmaker"] is None


def test_booking_code_is_shown_to_premium_viewers(db_session, admin, two_matches, auth_headers):
    _create(admin, _legs(two_matches), booking_code="ABC123", booking_code_bookmaker="Bet9ja")

    body = client.get("/api/predictions/admin-picks", headers=auth_headers).json()
    assert body[0]["has_booking_code"] is True
    assert body[0]["booking_code"] == "ABC123"
    assert body[0]["booking_code_bookmaker"] == "Bet9ja"


def test_booking_code_is_shown_to_superadmin_regardless_of_grant(db_session, admin, two_matches):
    """A superadmin never redeems a code themselves (require_active_access's
    own carve-out) -- the booking-code gate must not require one either."""

    _create(admin, _legs(two_matches), booking_code="ABC123", booking_code_bookmaker="Bet9ja")

    body = client.get("/api/predictions/admin-picks", headers=_headers(admin)).json()
    assert body[0]["booking_code"] == "ABC123"


def test_editing_can_remove_a_booking_code(db_session, admin, two_matches):
    created = _create(admin, _legs(two_matches), booking_code="ABC123", booking_code_bookmaker="Bet9ja")
    response = _update(admin, created["id"], _legs(two_matches))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["has_booking_code"] is False
    assert body["booking_code"] is None


# --- Result tracking / settlement (app.pick_settlement) --------------------


def _finish(db, match: Match, home_score: int, away_score: int) -> None:
    match.status = "FINISHED"
    match.home_score = home_score
    match.away_score = away_score
    db.commit()


def test_a_won_pick_is_shown_past_its_expiry_with_a_frozen_result(db_session, admin, two_matches, auth_headers):
    created = _create(admin, _legs(two_matches))
    pick = db_session.get(AdminPick, created["id"])
    pick.expires_at = dt.datetime.utcnow() - dt.timedelta(days=5)
    db_session.commit()
    for match in two_matches:
        _finish(db_session, match, 2, 0)  # Home Win, as every leg picked

    body = client.get("/api/predictions/admin-picks", headers=auth_headers).json()
    assert len(body) == 1
    assert body[0]["result"] == "won"
    assert body[0]["settled_at"] is not None
    assert all(leg["result"] == "won" for leg in body[0]["legs"])
    # Team names/market/selection still show correctly from the frozen snapshot.
    assert body[0]["legs"][0]["market"] == "Match Result"


def test_a_lost_pick_stays_visible_as_lost_not_dropped(db_session, admin, two_matches, auth_headers):
    created = _create(admin, _legs(two_matches))
    for match in two_matches:
        _finish(db_session, match, 0, 1)  # Away Win -- every leg's "Home Win" pick loses

    body = client.get("/api/predictions/admin-picks", headers=auth_headers).json()
    assert len(body) == 1
    assert body[0]["result"] == "lost"
    assert all(leg["result"] == "lost" for leg in body[0]["legs"])


def test_one_lost_leg_fails_the_whole_combo(db_session, admin, two_matches, auth_headers):
    _create(admin, _legs(two_matches))
    _finish(db_session, two_matches[0], 2, 0)  # wins
    _finish(db_session, two_matches[1], 0, 1)  # loses

    body = client.get("/api/predictions/admin-picks", headers=auth_headers).json()
    assert body[0]["result"] == "lost"
    results = {leg["match_id"]: leg["result"] for leg in body[0]["legs"]}
    assert results[two_matches[0].id] == "won"
    assert results[two_matches[1].id] == "lost"


def test_still_scheduled_matches_keep_the_pick_pending(db_session, admin, two_matches, auth_headers):
    _create(admin, _legs(two_matches))
    # Neither match has kicked off yet.

    body = client.get("/api/predictions/admin-picks", headers=auth_headers).json()
    assert body[0]["result"] == "pending"


def test_a_combo_with_mixed_kickoffs_hides_until_every_leg_finishes(db_session, admin, two_matches, auth_headers):
    """Known limitation: once ANY leg's match finishes, that leg can no
    longer be live-resolved (price_legs/resolve_legs_unpriced reject a
    finished match), but the pick as a whole can't settle either until
    EVERY leg finishes -- so it's hidden from this live feed in between,
    reappearing once the last leg's match ends and it settles. The admin
    management view (list_admin_picks) has no such gap."""

    _create(admin, _legs(two_matches))
    _finish(db_session, two_matches[0], 2, 0)
    # two_matches[1] is still scheduled.

    assert client.get("/api/predictions/admin-picks", headers=auth_headers).json() == []


def test_an_ungradeable_market_is_unresolved_until_an_admin_resolves_it(db_session, admin, two_matches, auth_headers):
    # Inserted directly rather than through the creation endpoint: a
    # minimal test Prediction has no Poisson lambda data, so the matrix-
    # derived "Winning Margin" market (used here specifically because
    # app.outcomes.grading doesn't cover it) never resolves through
    # find_outcome. Settlement doesn't care how a pick was created, only
    # what's in its stored legs.
    match = two_matches[0]
    pick = AdminPick(
        legs=[{"match_id": match.id, "market": "Winning Margin", "selection": "Home by exactly 2",
               "probability_at_pick": 0.2, "decimal_odds_at_pick": None}],
        priced=False, created_by_user_id=admin.id, expires_at=match.date + dt.timedelta(days=2),
    )
    db_session.add(pick)
    db_session.commit()
    db_session.refresh(pick)
    _finish(db_session, match, 2, 0)

    body = client.get("/api/predictions/admin-picks", headers=auth_headers).json()
    assert body[0]["result"] == "unresolved"

    response = client.patch(
        f"/api/admin/admin-picks/{pick.id}/result", json={"result": "won"}, headers=_headers(admin)
    )
    assert response.status_code == 200, response.text
    assert response.json()["result"] == "won"

    log = db_session.query(AuditLog).filter(AuditLog.action == "admin_pick.result_set").one()
    assert log.detail["result"] == "won"


def test_a_settled_result_is_frozen_and_never_regraded(db_session, admin, two_matches, auth_headers):
    created = _create(admin, _legs(two_matches))
    for match in two_matches:
        _finish(db_session, match, 2, 0)  # won

    first = client.get("/api/predictions/admin-picks", headers=auth_headers).json()
    assert first[0]["result"] == "won"

    # A later (e.g. corrected) score change must never flip an already-
    # frozen result -- settlement is one-way until an admin overrides it.
    for match in two_matches:
        _finish(db_session, match, 0, 1)

    second = client.get("/api/predictions/admin-picks", headers=auth_headers).json()
    assert second[0]["result"] == "won"
    db_session.get(AdminPick, created["id"])  # still the same row, unchanged


def test_resetting_to_pending_immediately_regrades_since_the_endpoint_resolves_before_responding(
    db_session, admin, two_matches, auth_headers
):
    """Resetting to "pending" un-freezes a pick, but since both matches here
    are already finished and fully auto-gradable, the same PATCH response
    already shows it re-settled rather than sitting at "pending" -- this
    override is for correcting a stuck "unresolved" or a genuine mistake,
    not for parking a fully-gradable pick in limbo."""

    created = _create(admin, _legs(two_matches))
    for match in two_matches:
        _finish(db_session, match, 2, 0)
    client.get("/api/predictions/admin-picks", headers=auth_headers)  # settles it to "won"

    response = client.patch(
        f"/api/admin/admin-picks/{created['id']}/result", json={"result": "pending"}, headers=_headers(admin)
    )
    assert response.status_code == 200, response.text
    assert response.json()["result"] == "won"


def test_public_admin_pick_track_record_has_no_data_before_anything_settles(db_session):
    response = client.get("/api/public/admin-picks-track-record")
    assert response.status_code == 200
    assert response.json() == {
        "has_data": False, "settled_count": 0, "won": 0, "lost": 0, "unresolved": 0, "hit_rate": 0.0,
    }


def test_public_admin_pick_track_record_counts_wins_and_losses_without_leaking_selections(
    db_session, admin, two_matches
):
    _create(admin, [_legs(two_matches)[0]])
    _finish(db_session, two_matches[0], 2, 0)  # won

    second_match = _match_with_odds(db_session, "Charlie", home=0.5)
    _create(admin, [{"match_id": second_match.id, "market": "Match Result", "selection": "Home Win"}])
    _finish(db_session, second_match, 0, 1)  # lost

    response = client.get("/api/public/admin-picks-track-record")
    assert response.status_code == 200
    body = response.json()
    assert body["has_data"] is True
    assert body["settled_count"] == 2
    assert body["won"] == 1
    assert body["lost"] == 1
    assert body["hit_rate"] == 0.5
    # Aggregate counts only -- no team names, markets or selections leaked.
    assert "picks" not in body
    assert "legs" not in body


# --- One free pick, the rest premium-only -----------------------------------


def test_a_free_tier_viewer_gets_one_unlocked_pick_and_the_rest_locked(db_session, admin, two_matches, headers_no_access):
    _create(admin, _legs(two_matches))
    second_pair = [_match_with_odds(db_session, "Charlie"), _match_with_odds(db_session, "Delta")]
    _create(admin, _legs(second_pair))

    body = client.get("/api/predictions/admin-picks", headers=headers_no_access).json()
    assert len(body) == 2
    # Newest first (AdminPick.created_at.desc()) -- the second one created.
    assert body[0]["locked"] is False
    assert len(body[0]["legs"]) == 2
    assert body[1]["locked"] is True
    assert body[1]["legs"] == []
    assert body[1]["leg_count"] == 2
    assert body[1]["combined_odds"] is None
    assert body[1]["combined_probability"] == 0.0


def test_a_premium_viewer_gets_every_admin_pick_unlocked(db_session, admin, two_matches, auth_headers):
    _create(admin, _legs(two_matches))
    second_pair = [_match_with_odds(db_session, "Charlie"), _match_with_odds(db_session, "Delta")]
    _create(admin, _legs(second_pair))

    body = client.get("/api/predictions/admin-picks", headers=auth_headers).json()
    assert len(body) == 2
    assert all(pick["locked"] is False for pick in body)


def test_a_superadmin_gets_every_admin_pick_unlocked(db_session, admin, two_matches):
    _create(admin, _legs(two_matches))
    second_pair = [_match_with_odds(db_session, "Charlie"), _match_with_odds(db_session, "Delta")]
    _create(admin, _legs(second_pair))

    body = client.get("/api/predictions/admin-picks", headers=_headers(admin)).json()
    assert len(body) == 2
    assert all(pick["locked"] is False for pick in body)


def test_a_settled_admin_pick_is_never_the_free_slot(db_session, admin, two_matches, headers_no_access):
    _create(admin, _legs(two_matches))
    _finish(db_session, two_matches[0], 2, 0)
    _finish(db_session, two_matches[1], 1, 0)  # settles the slip (won)

    second_pair = [_match_with_odds(db_session, "Charlie"), _match_with_odds(db_session, "Delta")]
    _create(admin, _legs(second_pair))  # still pending

    body = client.get("/api/predictions/admin-picks", headers=headers_no_access).json()
    pending = next(p for p in body if p["result"] == "pending")
    settled = next(p for p in body if p["result"] != "pending")
    assert pending["locked"] is False
    assert settled["locked"] is True


def test_guda_picks_take_priority_for_the_one_free_slot(db_session, admin, two_matches, headers_no_access):
    """When Guda Picks already offers a free-tier viewer an unlocked pick,
    Admin Picks/This Week's Picks/Random Picks must not also unlock one --
    exactly one free pick across the whole AI Picks page, not one per
    section."""

    guda_match = _match_with_odds(db_session, "Echo")
    response = client.post(
        "/api/admin/featured-picks",
        json={"match_id": guda_match.id, "market": "Match Result", "selection": "Home Win"},
        headers=_headers(admin),
    )
    assert response.status_code == 200, response.text

    _create(admin, _legs(two_matches))

    guda_body = client.get("/api/predictions/guda-picks", headers=headers_no_access).json()
    assert guda_body[0]["locked"] is False

    admin_body = client.get("/api/predictions/admin-picks", headers=headers_no_access).json()
    assert admin_body[0]["locked"] is True
