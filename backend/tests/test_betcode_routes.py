"""The API surface: /suggest and /resolve are free, stateless previews of
what a slip would contain (no bookmaker price anywhere), and /picks is the
one call that books a real code with a betting site.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app.db.models import Match, MatchOdds, Prediction, Team
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
def upcoming_match(db_session):
    """A scheduled match with a stored prediction -- no MatchOdds row, since
    nothing under test needs one. A separate MatchOdds row is added by
    the priced_match fixture only where a test explicitly wants to prove a
    stored price is irrelevant to these routes."""

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
    db_session.commit()
    return m


@pytest.fixture()
def priced_match(db_session, upcoming_match):
    """Same as upcoming_match, plus a stored MatchOdds row -- for a test
    proving these routes book a pick with or without one."""

    db_session.add(MatchOdds(
        match_id=upcoming_match.id, bookmaker="Bet9ja", market="Match Result", selection="Home Win",
        decimal_odds=1.30,
    ))
    db_session.commit()
    return upcoming_match


# --- /suggest -----------------------------------------------------------------


def test_suggest_requires_authentication():
    assert client.post("/api/betcodes/suggest", json={}).status_code == 401


def test_suggest_requires_active_access(headers_no_access):
    response = client.post("/api/betcodes/suggest", json={}, headers=headers_no_access)
    assert response.status_code == 403


def test_suggest_returns_no_priced_fields(auth_headers, upcoming_match):
    response = client.post("/api/betcodes/suggest", json={"min_probability": 0.5}, headers=auth_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["legs"]) == 1
    leg = body["legs"][0]
    assert leg["home_team"] == "Arsenal"
    assert "decimal_odds" not in leg
    assert "priced_by" not in leg
    assert "combined_odds" not in body


def test_suggest_does_not_need_a_stored_price(auth_headers, upcoming_match):
    """The whole point of dropping odds from this page: a match with no
    MatchOdds row at all still qualifies."""

    response = client.post("/api/betcodes/suggest", json={"min_probability": 0.5}, headers=auth_headers)
    assert response.status_code == 200, response.text
    assert len(response.json()["legs"]) == 1


def test_suggest_leagues_plural_reaches_the_engine(auth_headers, db_session, upcoming_match):
    home = Team(name="Real Madrid", league="Spanish La Liga", aliases=[])
    away = Team(name="Barcelona", league="Spanish La Liga", aliases=[])
    db_session.add_all([home, away])
    db_session.commit()
    for t in (home, away):
        db_session.refresh(t)
    m = Match(
        league="Spanish La Liga", season="2025-26", date=BASE + dt.timedelta(days=1),
        home_team_id=home.id, away_team_id=away.id, status="SCHEDULED",
    )
    db_session.add(m)
    db_session.commit()
    db_session.refresh(m)
    db_session.add(_prediction(m.id))
    db_session.commit()

    only_epl = client.post(
        "/api/betcodes/suggest",
        json={"min_probability": 0.5, "max_legs": 10, "leagues": ["English Premier League"]},
        headers=auth_headers,
    ).json()
    assert {leg["league"] for leg in only_epl["legs"]} == {"English Premier League"}

    both = client.post(
        "/api/betcodes/suggest",
        json={"min_probability": 0.5, "max_legs": 10, "leagues": ["English Premier League", "Spanish La Liga"]},
        headers=auth_headers,
    ).json()
    assert {leg["league"] for leg in both["legs"]} == {"English Premier League", "Spanish La Liga"}


def test_suggest_with_no_qualifying_match_explains_why(auth_headers, db_session):
    response = client.post("/api/betcodes/suggest", json={"min_probability": 0.99}, headers=auth_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["legs"] == []
    assert len(body["warnings"]) == 1
    assert "clears" in body["warnings"][0].lower()


# --- /resolve -------------------------------------------------------------------


def test_resolve_requires_authentication():
    assert client.post("/api/betcodes/resolve", json={"picks": []}).status_code == 401


def test_resolve_requires_active_access(headers_no_access):
    response = client.post("/api/betcodes/resolve", json={"picks": []}, headers=headers_no_access)
    assert response.status_code == 403


def test_resolve_checks_an_explicit_pick_against_the_current_prediction(auth_headers, upcoming_match):
    payload = {"picks": [{"match_id": upcoming_match.id, "market": "Match Result", "selection": "Home Win"}]}
    response = client.post("/api/betcodes/resolve", json=payload, headers=auth_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["legs"]) == 1
    assert body["legs"][0]["model_probability"] == pytest.approx(0.85)
    assert "decimal_odds" not in body["legs"][0]
    assert body["warnings"] == []


def test_resolve_reports_why_a_pick_was_skipped(auth_headers, upcoming_match):
    payload = {"picks": [{"match_id": upcoming_match.id, "market": "Match Result", "selection": "Nonsense"}]}
    response = client.post("/api/betcodes/resolve", json=payload, headers=auth_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["legs"] == []
    assert len(body["warnings"]) == 1
    assert "isn't an outcome" in body["warnings"][0]


def test_resolve_does_not_need_a_stored_price(auth_headers, priced_match):
    """Unlike the old /price, a market with no MatchOdds row still resolves
    -- this is what a Markets-page shortlist relies on."""

    payload = {"picks": [{"match_id": priced_match.id, "market": "Both Teams To Score", "selection": "Yes"}]}
    response = client.post("/api/betcodes/resolve", json=payload, headers=auth_headers)
    assert response.status_code == 200, response.text
    assert len(response.json()["legs"]) == 1


# --- booking a member's picks with a site --------------------------------------


class _FakeConnector:
    """Stands in for a site connection: issues a fixed code, optionally
    leaving some matches out, or fails with a site's own message."""

    def __init__(self, code="ABC123", unavailable=(), error=None):
        self.code, self.unavailable, self.error = code, list(unavailable), error

    def create_code(self, legs):
        from app.betcode.sites import BookingCodeError, ConnectorResult

        if self.error:
            raise BookingCodeError(self.error)
        return ConnectorResult(code=self.code, link=f"https://example.test/?code={self.code}",
                               unavailable_match_ids=self.unavailable)


class _RecordingConnector(_FakeConnector):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.booked = []

    def create_code(self, legs):
        self.booked.append(list(legs))
        return super().create_code(legs)


@pytest.fixture()
def sites(monkeypatch):
    """Swaps the site registry for one whose connections are fakes."""

    from app.betcode import sites as betting_sites

    def install(**connectors):
        registry = [
            betting_sites.Site(key, key.replace("_", " ").title(), (lambda c=c: c) if c else None)
            for key, c in connectors.items()
        ]
        monkeypatch.setattr(betting_sites, "SITES", registry)
        monkeypatch.setattr(betting_sites, "SITES_BY_KEY", {s.key: s for s in registry})

    return install


def test_sites_lists_which_are_connected(auth_headers, sites):
    sites(sportybet_gh=_FakeConnector(), betway_gh=None)

    body = client.get("/api/betcodes/sites", headers=auth_headers).json()

    assert body == [
        {"key": "sportybet_gh", "name": "Sportybet Gh", "connected": True},
        {"key": "betway_gh", "name": "Betway Gh", "connected": False},
    ]


def test_the_real_registry_claims_no_connection_it_does_not_have(auth_headers):
    """Until a site's connection is built, it must say so -- the booking
    step is hidden on that basis, and a false 'connected' would offer
    members a button that can only fail."""

    body = client.get("/api/betcodes/sites", headers=auth_headers).json()

    assert {s["key"] for s in body} == {"sportybet_gh"}
    from app.betcode import sites as betting_sites

    assert all(s["connected"] == betting_sites.SITES_BY_KEY[s["key"]].connected for s in body)


def test_booking_picks_requires_authentication():
    assert client.post("/api/betcodes/picks", json={"picks": [], "sites": ["sportybet_gh"]}).status_code == 401


def test_a_picked_outcome_with_no_stored_price_is_still_booked(auth_headers, upcoming_match, sites):
    """The site prices its own slip, so having no quote for BTTS here is no
    reason to leave the pick out -- unlike the old /price, which needed
    one."""

    connector = _RecordingConnector("MINE01")
    sites(sportybet_gh=connector)

    response = client.post(
        "/api/betcodes/picks",
        json={"picks": [{"match_id": upcoming_match.id, "market": "Both Teams To Score", "selection": "Yes"}],
              "sites": ["sportybet_gh"]},
        headers=auth_headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["site_codes"][0]["code"] == "MINE01"
    assert [(leg["market"], leg["selection"]) for leg in body["legs"]] == [("Both Teams To Score", "Yes")]
    assert connector.booked[0][0].decimal_odds is None


def test_a_pick_that_cant_be_booked_is_explained_and_the_rest_go_through(auth_headers, upcoming_match, sites):
    connector = _RecordingConnector()
    sites(sportybet_gh=connector)

    body = client.post(
        "/api/betcodes/picks",
        json={"picks": [
            {"match_id": upcoming_match.id, "market": "Match Result", "selection": "Home Win"},
            {"match_id": 999999, "market": "Match Result", "selection": "Home Win"},
        ], "sites": ["sportybet_gh"]},
        headers=auth_headers,
    ).json()

    assert [leg["match_id"] for leg in body["legs"]] == [upcoming_match.id]
    assert any("999999" in w for w in body["warnings"])
    assert [leg.match_id for leg in connector.booked[0]] == [upcoming_match.id]


def test_no_bookable_pick_is_a_422_and_no_site_is_asked(auth_headers, upcoming_match, sites):
    connector = _RecordingConnector()
    sites(sportybet_gh=connector)

    response = client.post(
        "/api/betcodes/picks",
        json={"picks": [{"match_id": upcoming_match.id, "market": "Match Result", "selection": "Nonsense"}],
              "sites": ["sportybet_gh"]},
        headers=auth_headers,
    )

    assert response.status_code == 422
    assert "isn't an outcome" in response.json()["detail"]
    assert connector.booked == []


def test_booking_picks_needs_a_site(auth_headers, upcoming_match):
    response = client.post(
        "/api/betcodes/picks",
        json={"picks": [{"match_id": upcoming_match.id, "market": "Match Result", "selection": "Home Win"}],
              "sites": []},
        headers=auth_headers,
    )

    assert response.status_code == 422


def test_one_slip_gets_a_code_from_each_site(auth_headers, upcoming_match, sites):
    sites(
        sportybet_gh=_FakeConnector("SPORTY1"),
        betway_gh=_FakeConnector(error="Betway refused the slip."),
        onexbet=None,
    )

    response = client.post(
        "/api/betcodes/picks",
        json={"picks": [{"match_id": upcoming_match.id, "market": "Match Result", "selection": "Home Win"}],
              "sites": ["sportybet_gh", "betway_gh", "onexbet"]},
        headers=auth_headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    by_site = {c["site"]: c for c in body["site_codes"]}
    assert by_site["sportybet_gh"]["status"] == "code_ready" and by_site["sportybet_gh"]["code"] == "SPORTY1"
    assert by_site["betway_gh"]["status"] == "error" and "refused" in by_site["betway_gh"]["message"]
    assert by_site["onexbet"]["status"] == "not_connected"


def test_a_site_that_skips_a_match_says_which(auth_headers, upcoming_match, sites):
    sites(sportybet_gh=_FakeConnector("PART01", unavailable=[upcoming_match.id]))

    body = client.post(
        "/api/betcodes/picks",
        json={"picks": [{"match_id": upcoming_match.id, "market": "Match Result", "selection": "Home Win"}],
              "sites": ["sportybet_gh"]},
        headers=auth_headers,
    ).json()

    code = body["site_codes"][0]
    assert code["unavailable_match_ids"] == [upcoming_match.id]
    assert "couldn't take 1 of these 1 picks" in code["message"]


def test_no_connected_site_books_nothing_without_inventing_a_code(auth_headers, upcoming_match, sites):
    sites(sportybet_gh=None)

    body = client.post(
        "/api/betcodes/picks",
        json={"picks": [{"match_id": upcoming_match.id, "market": "Match Result", "selection": "Home Win"}],
              "sites": ["sportybet_gh"]},
        headers=auth_headers,
    ).json()

    assert body["site_codes"][0]["status"] == "not_connected"
    assert body["site_codes"][0]["code"] is None


# --- Every booked code is recorded, and its results tracked -----------------


def _book(headers, match, market="Match Result", selection="Home Win", sites_list=("sportybet_gh",)):
    return client.post(
        "/api/betcodes/picks",
        json={"picks": [{"match_id": match.id, "market": market, "selection": selection}], "sites": list(sites_list)},
        headers=headers,
    )


def test_booking_a_pick_records_it_against_the_members_account(db_session, auth_headers, upcoming_match, sites):
    from app.db.models import BookingSlip, User

    sites(sportybet_gh=_FakeConnector("REC001"))
    response = _book(auth_headers, upcoming_match)
    assert response.status_code == 200, response.text

    user = db_session.query(User).filter(User.email == "test-user@example.com").one()
    slips = db_session.query(BookingSlip).filter(BookingSlip.user_id == user.id).all()
    assert len(slips) == 1
    assert slips[0].status == "code_ready"
    assert slips[0].booking_code == "REC001"
    assert slips[0].result == "pending"
    assert slips[0].legs[0]["market"] == "Match Result"


def test_my_codes_requires_authentication():
    assert client.get("/api/betcodes/mine").status_code == 401


def test_my_codes_lists_most_recent_first(auth_headers, upcoming_match, sites):
    sites(sportybet_gh=_FakeConnector("FIRST01"))
    _book(auth_headers, upcoming_match)
    sites(sportybet_gh=_FakeConnector("SECOND1"))
    _book(auth_headers, upcoming_match, market="Both Teams To Score", selection="Yes")

    body = client.get("/api/betcodes/mine", headers=auth_headers).json()
    assert len(body) == 2
    assert body[0]["legs"][0]["selection"] == "Yes"  # most recent first
    assert body[0]["result"] == "pending"
    assert body[0]["site_codes"][0]["code"] == "SECOND1"


def test_my_codes_only_shows_the_calling_members_own_slips(db_session, auth_headers, upcoming_match, sites):
    from app.auth.passwords import hash_password
    from app.auth.tokens import create_token
    from app.config import get_settings
    from app.db.models import User
    from tests.conftest import grant_active_access

    other = User(
        email="other-member@example.com", name="Other", password_hash=hash_password("a-good-password"),
        role="user", status="active",
    )
    db_session.add(other)
    db_session.commit()
    db_session.refresh(other)
    grant_active_access(db_session, other)
    settings = get_settings()
    other_headers = {
        "Authorization": f"Bearer {create_token({'user_id': other.id, 'role': other.role}, settings.secret_key, settings.session_ttl_seconds)}"
    }

    sites(sportybet_gh=_FakeConnector())
    _book(auth_headers, upcoming_match)
    _book(other_headers, upcoming_match, market="Both Teams To Score", selection="Yes")

    mine = client.get("/api/betcodes/mine", headers=auth_headers).json()
    assert len(mine) == 1
    assert mine[0]["legs"][0]["selection"] == "Home Win"

    others = client.get("/api/betcodes/mine", headers=other_headers).json()
    assert len(others) == 1
    assert others[0]["legs"][0]["selection"] == "Yes"


def test_a_settled_won_code_shows_in_my_codes_history(db_session, auth_headers, upcoming_match, sites):
    sites(sportybet_gh=_FakeConnector("WON0001"))
    _book(auth_headers, upcoming_match)

    upcoming_match.status = "FINISHED"
    upcoming_match.home_score = 2
    upcoming_match.away_score = 0
    db_session.commit()

    body = client.get("/api/betcodes/mine", headers=auth_headers).json()
    assert body[0]["result"] == "won"
    assert body[0]["settled_at"] is not None
    assert body[0]["legs"][0]["result"] == "won"


def test_a_settled_lost_code_stays_visible_as_lost(db_session, auth_headers, upcoming_match, sites):
    sites(sportybet_gh=_FakeConnector("LOST0001"))
    _book(auth_headers, upcoming_match)  # picked Home Win

    upcoming_match.status = "FINISHED"
    upcoming_match.home_score = 0
    upcoming_match.away_score = 2
    db_session.commit()

    body = client.get("/api/betcodes/mine", headers=auth_headers).json()
    assert body[0]["result"] == "lost"
    assert body[0]["legs"][0]["result"] == "lost"
