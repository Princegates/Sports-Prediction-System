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


def test_preview_leagues_plural_reaches_the_engine(auth_headers, db_session, priced_match):
    """The multi-select league field on the API payload must actually reach
    SlipCriteria.leagues, not just parse -- a La Liga fixture only shows up
    when La Liga is one of the leagues asked for."""

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
    db_session.add(MatchOdds(
        match_id=m.id, bookmaker="Bet9ja", market="Match Result", selection="Home Win", decimal_odds=1.40,
    ))
    db_session.commit()

    # target_odds high enough that the search never stops early -- otherwise
    # a single 1.30-odds EPL leg alone would already clear CRITERIA's own
    # 1.2 target and mask whether La Liga was even in the candidate pool.
    overrides = {"max_legs": 10, "target_odds": 1_000_000.0}

    only_epl = client.post(
        "/api/betcodes/preview", json=CRITERIA | overrides | {"leagues": ["English Premier League"]},
        headers=auth_headers,
    ).json()
    assert {leg["league"] for leg in only_epl["legs"]} == {"English Premier League"}

    both = client.post(
        "/api/betcodes/preview",
        json=CRITERIA | overrides | {"leagues": ["English Premier League", "Spanish La Liga"]},
        headers=auth_headers,
    ).json()
    assert {leg["league"] for leg in both["legs"]} == {"English Premier League", "Spanish La Liga"}


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


# --- codes on betting sites ---------------------------------------------------


class _FakeConnector:
    """Stands in for a site connection: issues a fixed code, optionally
    leaving some matches out, or fails with a site's own message."""

    def __init__(self, code="ABC123", unavailable=(), error=None):
        self.code, self.unavailable, self.error = code, list(unavailable), error

    def create_code(self, legs):
        from app.betcode.providers import BookingCodeError
        from app.betcode.sites import ConnectorResult

        if self.error:
            raise BookingCodeError(self.error)
        return ConnectorResult(code=self.code, link=f"https://example.test/?code={self.code}",
                               unavailable_match_ids=self.unavailable)


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

    assert {s["key"] for s in body} == {"sportybet_gh", "betway_gh", "1xbet"}
    from app.betcode import sites as betting_sites

    assert all(s["connected"] == betting_sites.SITES_BY_KEY[s["key"]].connected for s in body)


def test_one_slip_gets_a_code_from_each_site(auth_headers, db_session, priced_match, sites):
    sites(
        sportybet_gh=_FakeConnector("SPORTY1"),
        betway_gh=_FakeConnector(error="Betway refused the slip."),
        onexbet=None,
    )

    response = client.post(
        "/api/betcodes",
        json={"criteria": CRITERIA, "sites": ["sportybet_gh", "betway_gh", "onexbet"]},
        headers=auth_headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    by_site = {c["site"]: c for c in body["site_codes"]}
    assert by_site["sportybet_gh"]["status"] == "code_ready" and by_site["sportybet_gh"]["code"] == "SPORTY1"
    assert by_site["betway_gh"]["status"] == "error" and "refused" in by_site["betway_gh"]["message"]
    assert by_site["onexbet"]["status"] == "not_connected"

    # The slip is saved with every site's answer, and the single-code
    # fields mirror the first site that issued one.
    slip = db_session.query(BookingSlip).one()
    assert slip.status == "code_ready" and slip.booking_code == "SPORTY1"
    assert len(slip.site_codes) == 3
    assert client.get(f"/api/betcodes/{slip.id}", headers=auth_headers).json()["site_codes"] == body["site_codes"]


def test_a_site_that_skips_a_match_says_which(auth_headers, priced_match, sites):
    sites(sportybet_gh=_FakeConnector("PART01", unavailable=[priced_match.id]))

    body = client.post(
        "/api/betcodes", json={"criteria": CRITERIA, "sites": ["sportybet_gh"]}, headers=auth_headers
    ).json()

    code = body["site_codes"][0]
    assert code["unavailable_match_ids"] == [priced_match.id]
    assert "doesn't offer 1 of these 1 picks" in code["message"]


def test_no_connected_site_saves_the_slip_without_inventing_a_code(auth_headers, db_session, priced_match, sites):
    sites(sportybet_gh=None)

    body = client.post(
        "/api/betcodes", json={"criteria": CRITERIA, "sites": ["sportybet_gh"]}, headers=auth_headers
    ).json()

    assert body["status"] == "provider_unavailable"
    assert body["booking_code"] is None and body["site_codes"][0]["code"] is None
    assert db_session.query(BookingSlip).count() == 1


# --- booking a member's own picks --------------------------------------------


class _RecordingConnector(_FakeConnector):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.booked = []

    def create_code(self, legs):
        self.booked.append(list(legs))
        return super().create_code(legs)


def test_booking_picks_requires_authentication():
    assert client.post("/api/betcodes/picks", json={"picks": [], "sites": ["sportybet_gh"]}).status_code == 401


def test_a_picked_outcome_with_no_stored_price_is_still_booked(auth_headers, db_session, priced_match, sites):
    """The site prices its own slip, so our lack of a quote for BTTS here is
    no reason to leave the pick out -- unlike /price, which needs one."""

    connector = _RecordingConnector("MINE01")
    sites(sportybet_gh=connector)

    response = client.post(
        "/api/betcodes/picks",
        json={"picks": [{"match_id": priced_match.id, "market": "Both Teams To Score", "selection": "Yes"}],
              "sites": ["sportybet_gh"]},
        headers=auth_headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["site_codes"][0]["code"] == "MINE01"
    assert [(leg["market"], leg["selection"]) for leg in body["legs"]] == [("Both Teams To Score", "Yes")]
    assert connector.booked[0][0].decimal_odds is None
    assert db_session.query(BookingSlip).count() == 0


def test_a_pick_that_cant_be_booked_is_explained_and_the_rest_go_through(auth_headers, priced_match, sites):
    connector = _RecordingConnector()
    sites(sportybet_gh=connector)

    body = client.post(
        "/api/betcodes/picks",
        json={"picks": [
            {"match_id": priced_match.id, "market": "Match Result", "selection": "Home Win"},
            {"match_id": 999999, "market": "Match Result", "selection": "Home Win"},
        ], "sites": ["sportybet_gh"]},
        headers=auth_headers,
    ).json()

    assert [leg["match_id"] for leg in body["legs"]] == [priced_match.id]
    assert any("999999" in w for w in body["warnings"])
    assert [leg.match_id for leg in connector.booked[0]] == [priced_match.id]


def test_no_bookable_pick_is_a_422_and_no_site_is_asked(auth_headers, priced_match, sites):
    connector = _RecordingConnector()
    sites(sportybet_gh=connector)

    response = client.post(
        "/api/betcodes/picks",
        json={"picks": [{"match_id": priced_match.id, "market": "Match Result", "selection": "Nonsense"}],
              "sites": ["sportybet_gh"]},
        headers=auth_headers,
    )

    assert response.status_code == 422
    assert "isn't an outcome" in response.json()["detail"]
    assert connector.booked == []


def test_booking_picks_needs_a_site(auth_headers, priced_match):
    response = client.post(
        "/api/betcodes/picks",
        json={"picks": [{"match_id": priced_match.id, "market": "Match Result", "selection": "Home Win"}], "sites": []},
        headers=auth_headers,
    )

    assert response.status_code == 422
