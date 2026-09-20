"""The API surface: preview is free and stateless, generate always persists a
slip whether or not a code came back, and history is scoped to the caller.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app.db.models import BookingSlip, Match, MatchOdds, Prediction, Team
from app.main import app

client = TestClient(app)
BASE = dt.datetime.utcnow() + dt.timedelta(hours=6)


def _prediction(match_id: int, home=0.85) -> Prediction:
    return Prediction(
        match_id=match_id, model_version="test", home_win=home, draw=(1 - home) * 0.6, away_win=(1 - home) * 0.4,
        over_probabilities={"2.5": 0.6}, btts_yes=0.55, btts_no=0.45,
        correct_score_probabilities={"2-1": 0.11}, most_likely_score="2-1", most_likely_score_probability=0.11,
        global_outcome_market="Match Result", global_outcome_selection="Home Win", global_outcome_probability=home,
        confidence="HIGH", data_quality_score=1.0, model_agreement_score=0.9,
        explanation={"positive": [], "negative": []}, model_breakdown={},
    )


@pytest.fixture()
def priced_match(db_session):
    home = Team(name="Arsenal", league="English Premier League", aliases=[])
    away = Team(name="Chelsea", league="English Premier League", aliases=[])
    db_session.add_all([home, away])
    db_session.commit()
    db_session.refresh(home)
    db_session.refresh(away)

    m = Match(
        league="English Premier League", season="2025-26", date=BASE + dt.timedelta(days=1),
        home_team_id=home.id, away_team_id=away.id, status="SCHEDULED",
    )
    db_session.add(m)
    db_session.commit()
    db_session.refresh(m)
    db_session.add(_prediction(m.id))
    db_session.add(MatchOdds(
        match_id=m.id, bookmaker="Bet9ja", market="Match Result", selection="Home Win", decimal_odds=1.30,
    ))
    db_session.commit()
    return m


CRITERIA = {"bookmaker": "Bet9ja", "target_odds": 1.2, "min_probability": 0.5}


def test_requires_authentication():
    assert client.post("/api/betcodes/preview", json={"criteria": CRITERIA} | CRITERIA).status_code == 401


def test_requires_active_access(headers_no_access):
    response = client.post("/api/betcodes/preview", json=CRITERIA, headers=headers_no_access)
    assert response.status_code == 403


def test_preview_returns_legs_and_writes_nothing(auth_headers, db_session, priced_match):
    response = client.post("/api/betcodes/preview", json=CRITERIA, headers=auth_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["legs"]) == 1
    assert body["legs"][0]["home_team"] == "Arsenal"
    assert body["combined_odds"] == pytest.approx(1.30, abs=0.001)
    assert db_session.query(BookingSlip).count() == 0


def test_price_requires_authentication():
    assert client.post("/api/betcodes/price", json={"picks": []}).status_code == 401


def test_price_requires_active_access(headers_no_access):
    response = client.post("/api/betcodes/price", json={"picks": []}, headers=headers_no_access)
    assert response.status_code == 403


def test_price_attaches_the_real_stored_quote_to_an_explicit_pick(auth_headers, priced_match):
    """The chat-picks / Markets-shortlist entry point: caller already knows
    exactly which (match, market, selection) it wants, this just prices it."""

    payload = {"picks": [{"match_id": priced_match.id, "market": "Match Result", "selection": "Home Win"}]}
    response = client.post("/api/betcodes/price", json=payload, headers=auth_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["legs"]) == 1
    assert body["legs"][0]["decimal_odds"] == pytest.approx(1.30, abs=0.001)
    assert body["legs"][0]["priced_by"] == "Bet9ja"
    assert body["combined_odds"] == pytest.approx(1.30, abs=0.001)
    assert body["met_target"] is True
    assert body["warnings"] == []


def test_price_reports_why_an_unpriceable_pick_was_skipped(auth_headers, priced_match):
    payload = {"picks": [{"match_id": priced_match.id, "market": "Both Teams To Score", "selection": "Yes"}]}
    response = client.post("/api/betcodes/price", json=payload, headers=auth_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["legs"] == []
    assert len(body["warnings"]) == 1
    assert "no stored bookmaker price" in body["warnings"][0].lower()


def test_generate_without_a_provider_still_saves_the_slip(auth_headers, db_session, priced_match):
    response = client.post("/api/betcodes", json={"criteria": CRITERIA}, headers=auth_headers)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["status"] == "provider_unavailable"
    assert body["booking_code"] is None
    assert "aggregator" in body["provider_message"].lower()
    # The real part -- the selections and combined price -- is still there.
    assert len(body["legs"]) == 1
    assert body["combined_odds"] == pytest.approx(1.30, abs=0.001)

    assert db_session.query(BookingSlip).count() == 1


def test_generate_from_explicit_legs_matches_what_preview_showed(auth_headers, priced_match):
    preview = client.post("/api/betcodes/preview", json=CRITERIA, headers=auth_headers).json()

    response = client.post(
        "/api/betcodes", json={"criteria": CRITERIA, "legs": preview["legs"]}, headers=auth_headers
    )
    body = response.json()
    assert body["legs"] == preview["legs"]
    assert body["combined_odds"] == pytest.approx(preview["combined_odds"])


def test_generate_with_no_qualifying_legs_is_a_422_not_an_empty_slip(auth_headers, db_session):
    response = client.post(
        "/api/betcodes", json={"criteria": {"bookmaker": "Bet9ja", "target_odds": 2.0}}, headers=auth_headers
    )
    assert response.status_code == 422
    assert db_session.query(BookingSlip).count() == 0


def test_history_is_scoped_to_the_caller(auth_headers, headers_no_access, priced_match, db_session, user_no_access):
    """headers_no_access belongs to an account with no access grant, so it
    cannot itself generate a slip -- a slip is inserted directly to prove the
    *listing* endpoint would still scope by owner if one existed."""

    client.post("/api/betcodes", json={"criteria": CRITERIA}, headers=auth_headers)

    other = BookingSlip(
        user_id=user_no_access.id, bookmaker="Bet9ja", criteria={}, legs=[],
        combined_odds=1.0, combined_probability=1.0, expires_at=dt.datetime.utcnow(),
        provider="none", status="provider_unavailable",
    )
    db_session.add(other)
    db_session.commit()

    mine = client.get("/api/betcodes", headers=auth_headers).json()
    assert len(mine) == 1
    assert all(row["id"] != other.id for row in mine)


def test_a_slip_cannot_be_read_by_a_different_user(auth_headers, db_session, user_no_access):
    other = BookingSlip(
        user_id=user_no_access.id, bookmaker="Bet9ja", criteria={}, legs=[],
        combined_odds=1.0, combined_probability=1.0, expires_at=dt.datetime.utcnow(),
        provider="none", status="provider_unavailable",
    )
    db_session.add(other)
    db_session.commit()
    db_session.refresh(other)

    response = client.get(f"/api/betcodes/{other.id}", headers=auth_headers)
    assert response.status_code == 404


def test_expires_at_is_the_earliest_leg_kickoff(auth_headers, db_session, priced_match):
    response = client.post("/api/betcodes", json={"criteria": CRITERIA}, headers=auth_headers)
    body = response.json()
    assert body["expires_at"][:16] == priced_match.date.isoformat()[:16]
