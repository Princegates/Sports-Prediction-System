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


def test_suggest_honors_an_explicit_market_outside_the_default_six(auth_headers, upcoming_match):
    """The bug this fixed: an explicitly requested market used to be
    silently narrowed back to DEFAULT_MARKETS (Match Result, Double Chance,
    Draw No Bet, Total Goals 1.5, Handicap -3/+3) if it wasn't one of those
    six -- so asking for Both Teams To Score (not in that list) used to
    come back as a Match Result leg instead. The Markets picker now offers
    the full catalog, and an explicit, deliberate choice from it should be
    honored as asked."""

    response = client.post(
        "/api/betcodes/suggest",
        json={"min_probability": 0.5, "markets": ["Both Teams To Score"]},
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    legs = response.json()["legs"]
    assert len(legs) == 1
    assert legs[0]["market"] == "Both Teams To Score"
    assert legs[0]["selection"] == "Yes"


def test_suggest_honors_an_explicit_league_outside_the_curated_eight(db_session, auth_headers):
    """The bug this fixed: an explicitly requested league used to be
    silently narrowed back to AI_PICKS_LEAGUES (the 8 biggest leagues) if
    it wasn't one of those -- so asking for Scottish Premiership (not in
    that list) used to search the full 8 instead, finding whatever
    unrelated match happened to qualify there rather than nothing or the
    league actually asked for."""

    home = Team(name="Celtic", league="Scottish Premiership", aliases=[])
    away = Team(name="Rangers", league="Scottish Premiership", aliases=[])
    db_session.add_all([home, away])
    db_session.commit()
    db_session.refresh(home)
    db_session.refresh(away)
    match = Match(
        league="Scottish Premiership", season="2025-26", date=BASE + dt.timedelta(days=1),
        home_team_id=home.id, away_team_id=away.id, status="SCHEDULED",
    )
    db_session.add(match)
    db_session.commit()
    db_session.refresh(match)
    db_session.add(_prediction(match.id, home=0.8))
    db_session.commit()

    response = client.post(
        "/api/betcodes/suggest",
        json={"min_probability": 0.5, "leagues": ["Scottish Premiership"]},
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    legs = response.json()["legs"]
    assert len(legs) == 1
    assert legs[0]["league"] == "Scottish Premiership"


def test_suggest_with_a_calendar_date_only_returns_that_day(db_session, auth_headers):
    """date_from (no date_to) means that one day, not "from then on" --
    a match a day later must not come back."""

    home = Team(name="Arsenal", league="English Premier League", aliases=[])
    away = Team(name="Chelsea", league="English Premier League", aliases=[])
    home2 = Team(name="Liverpool", league="English Premier League", aliases=[])
    away2 = Team(name="Everton", league="English Premier League", aliases=[])
    db_session.add_all([home, away, home2, away2])
    db_session.commit()
    for t in (home, away, home2, away2):
        db_session.refresh(t)

    target_day = (BASE + dt.timedelta(days=3)).date()
    on_target = Match(
        league="English Premier League", season="2025-26",
        date=dt.datetime.combine(target_day, dt.time(15, 0)),
        home_team_id=home.id, away_team_id=away.id, status="SCHEDULED",
    )
    day_after = Match(
        league="English Premier League", season="2025-26",
        date=dt.datetime.combine(target_day + dt.timedelta(days=1), dt.time(15, 0)),
        home_team_id=home2.id, away_team_id=away2.id, status="SCHEDULED",
    )
    db_session.add_all([on_target, day_after])
    db_session.commit()
    for m in (on_target, day_after):
        db_session.refresh(m)
    db_session.add(_prediction(on_target.id, home=0.8))
    db_session.add(_prediction(day_after.id, home=0.8))
    db_session.commit()

    response = client.post(
        "/api/betcodes/suggest",
        json={"min_probability": 0.5, "date_from": target_day.isoformat()},
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    legs = response.json()["legs"]
    assert len(legs) == 1
    assert legs[0]["home_team"] == "Arsenal"


def test_suggest_with_feature_all_markets_gives_every_selected_market_its_own_leg(db_session, auth_headers):
    """Default mode collapses each match to its single strongest qualifying
    outcome -- two matches whose strongest outcome is Match Result (85%)
    never surface their own Both Teams To Score leg (55%) instead, even
    though it clears min_probability and was explicitly selected.
    feature_all_markets=True instead gives each selected market its own
    leg, on its own match, whenever the data allows it."""

    home1 = Team(name="Arsenal", league="English Premier League", aliases=[])
    away1 = Team(name="Chelsea", league="English Premier League", aliases=[])
    home2 = Team(name="Liverpool", league="English Premier League", aliases=[])
    away2 = Team(name="Everton", league="English Premier League", aliases=[])
    db_session.add_all([home1, away1, home2, away2])
    db_session.commit()
    for t in (home1, away1, home2, away2):
        db_session.refresh(t)

    match1 = Match(
        league="English Premier League", season="2025-26", date=BASE + dt.timedelta(days=1),
        home_team_id=home1.id, away_team_id=away1.id, status="SCHEDULED",
    )
    match2 = Match(
        league="English Premier League", season="2025-26", date=BASE + dt.timedelta(days=1),
        home_team_id=home2.id, away_team_id=away2.id, status="SCHEDULED",
    )
    db_session.add_all([match1, match2])
    db_session.commit()
    for m in (match1, match2):
        db_session.refresh(m)
    db_session.add(_prediction(match1.id))
    db_session.add(_prediction(match2.id))
    db_session.commit()

    criteria = {"min_probability": 0.5, "markets": ["Match Result", "Both Teams To Score"]}

    default_response = client.post("/api/betcodes/suggest", json=criteria, headers=auth_headers)
    assert default_response.status_code == 200, default_response.text
    assert {leg["market"] for leg in default_response.json()["legs"]} == {"Match Result"}

    response = client.post(
        "/api/betcodes/suggest", json={**criteria, "feature_all_markets": True}, headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    legs = response.json()["legs"]
    assert len(legs) == 2
    assert {leg["market"] for leg in legs} == {"Match Result", "Both Teams To Score"}
    assert len({leg["match_id"] for leg in legs}) == 2


def test_feature_all_markets_fills_up_to_max_legs_not_just_one_per_market(db_session, auth_headers):
    """The bug this fixed: with two markets selected and a much higher leg
    cap, the slip used to stop at exactly 2 legs (one per market) even
    though more qualifying matches -- and a higher max_legs -- were
    available. Once every selected market has its guaranteed leg, the
    remaining capacity should be filled by cycling back through the
    markets instead of leaving the slip short of what was actually asked
    for."""

    teams = []
    matches = []
    for i in range(5):
        home = Team(name=f"Home{i}", league="English Premier League", aliases=[])
        away = Team(name=f"Away{i}", league="English Premier League", aliases=[])
        db_session.add_all([home, away])
        teams.extend([home, away])
    db_session.commit()
    for t in teams:
        db_session.refresh(t)

    for i in range(5):
        m = Match(
            league="English Premier League", season="2025-26", date=BASE + dt.timedelta(days=1),
            home_team_id=teams[2 * i].id, away_team_id=teams[2 * i + 1].id, status="SCHEDULED",
        )
        db_session.add(m)
        matches.append(m)
    db_session.commit()
    for m in matches:
        db_session.refresh(m)
        db_session.add(_prediction(m.id))
    db_session.commit()

    response = client.post(
        "/api/betcodes/suggest",
        json={
            "min_probability": 0.5,
            "markets": ["Match Result", "Both Teams To Score"],
            "max_legs": 4,
            "feature_all_markets": True,
        },
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    legs = response.json()["legs"]
    assert len(legs) == 4
    assert {leg["market"] for leg in legs} == {"Match Result", "Both Teams To Score"}
    assert len({leg["match_id"] for leg in legs}) == 4  # one leg per match, still


def test_suggest_exclude_match_ids_surfaces_a_different_match(db_session, auth_headers):
    """The bug Refresh fixed: safest-first search is deterministic, so
    running the exact same criteria again always landed on the identical
    match. exclude_match_ids -- what AI Generation's Refresh button sends
    back -- lets the same criteria surface the next-safest match instead."""

    home1 = Team(name="Arsenal", league="English Premier League", aliases=[])
    away1 = Team(name="Chelsea", league="English Premier League", aliases=[])
    home2 = Team(name="Liverpool", league="English Premier League", aliases=[])
    away2 = Team(name="Everton", league="English Premier League", aliases=[])
    db_session.add_all([home1, away1, home2, away2])
    db_session.commit()
    for t in (home1, away1, home2, away2):
        db_session.refresh(t)

    strongest = Match(
        league="English Premier League", season="2025-26", date=BASE + dt.timedelta(days=1),
        home_team_id=home1.id, away_team_id=away1.id, status="SCHEDULED",
    )
    runner_up = Match(
        league="English Premier League", season="2025-26", date=BASE + dt.timedelta(days=1),
        home_team_id=home2.id, away_team_id=away2.id, status="SCHEDULED",
    )
    db_session.add_all([strongest, runner_up])
    db_session.commit()
    for m in (strongest, runner_up):
        db_session.refresh(m)
    db_session.add(_prediction(strongest.id, home=0.85))
    db_session.add(_prediction(runner_up.id, home=0.75))
    db_session.commit()

    criteria = {"min_probability": 0.5, "markets": ["Match Result"], "max_legs": 1}

    first = client.post("/api/betcodes/suggest", json=criteria, headers=auth_headers)
    assert first.status_code == 200, first.text
    first_legs = first.json()["legs"]
    assert len(first_legs) == 1
    assert first_legs[0]["match_id"] == strongest.id  # the safer of the two, as expected

    same_again = client.post("/api/betcodes/suggest", json=criteria, headers=auth_headers)
    assert same_again.json()["legs"][0]["match_id"] == strongest.id  # deterministic: identical result

    refreshed = client.post(
        "/api/betcodes/suggest", json={**criteria, "exclude_match_ids": [strongest.id]}, headers=auth_headers,
    )
    assert refreshed.status_code == 200, refreshed.text
    refreshed_legs = refreshed.json()["legs"]
    assert len(refreshed_legs) == 1
    assert refreshed_legs[0]["match_id"] == runner_up.id  # the one Refresh should surface instead


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


# --- /suggest-random ------------------------------------------------------------


@pytest.fixture()
def many_matches(db_session):
    """25 independent 85%-favored matches -- comfortably more than the
    20-leg cap, so a random draw always has something to pick from
    regardless of which ones the shuffle lands on."""

    matches = []
    for i in range(25):
        home = Team(name=f"Home{i}", league="English Premier League", aliases=[])
        away = Team(name=f"Away{i}", league="English Premier League", aliases=[])
        db_session.add_all([home, away])
        db_session.commit()
        for t in (home, away):
            db_session.refresh(t)
        m = Match(
            league="English Premier League", season="2025-26", date=BASE + dt.timedelta(days=1, hours=i),
            home_team_id=home.id, away_team_id=away.id, status="SCHEDULED",
        )
        db_session.add(m)
        db_session.commit()
        db_session.refresh(m)
        db_session.add(_prediction(m.id))
        db_session.commit()
        matches.append(m)
    return matches


def test_suggest_random_requires_authentication():
    assert client.post("/api/betcodes/suggest-random", json={}).status_code == 401


def test_suggest_random_requires_active_access(headers_no_access):
    response = client.post("/api/betcodes/suggest-random", json={}, headers=headers_no_access)
    assert response.status_code == 403


def test_suggest_random_lands_between_ten_and_twenty_legs(auth_headers, many_matches):
    response = client.post("/api/betcodes/suggest-random", json={}, headers=auth_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert 10 <= len(body["legs"]) <= 20


def test_suggest_random_returns_no_priced_fields(auth_headers, many_matches):
    response = client.post("/api/betcodes/suggest-random", json={}, headers=auth_headers)
    assert response.status_code == 200, response.text
    leg = response.json()["legs"][0]
    assert "decimal_odds" not in leg
    assert "priced_by" not in leg
    assert "combined_odds" not in response.json()


def test_suggest_random_clamps_an_out_of_range_leg_count(auth_headers, many_matches):
    response = client.post(
        "/api/betcodes/suggest-random", json={"min_legs": 1, "max_legs": 1000}, headers=auth_headers
    )
    assert response.status_code == 200, response.text
    assert 10 <= len(response.json()["legs"]) <= 20


def test_suggest_random_with_no_qualifying_match_explains_why(auth_headers, db_session):
    response = client.post("/api/betcodes/suggest-random", json={"min_probability": 0.99}, headers=auth_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["legs"] == []
    assert len(body["warnings"]) >= 1


def test_suggest_random_result_can_be_booked(auth_headers, many_matches, sites):
    suggested = client.post("/api/betcodes/suggest-random", json={}, headers=auth_headers)
    assert suggested.status_code == 200, suggested.text
    picks = [{"match_id": leg["match_id"], "market": leg["market"], "selection": leg["selection"]} for leg in suggested.json()["legs"]]

    response = client.post(
        "/api/betcodes/picks", json={"picks": picks, "sites": ["sportybet_gh"]}, headers=auth_headers
    )
    assert response.status_code == 200, response.text
    assert len(response.json()["legs"]) == len(picks)


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

    assert {s["key"] for s in body} == {"sportybet_gh", "msport_gh", "football_gh"}
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


def test_booking_a_pick_is_audit_logged(db_session, auth_headers, upcoming_match, sites):
    from app.db.models import AuditLog, User

    sites(sportybet_gh=_FakeConnector("AUDIT01"))
    response = _book(auth_headers, upcoming_match)
    assert response.status_code == 200, response.text

    user = db_session.query(User).filter(User.email == "test-user@example.com").one()
    entry = db_session.query(AuditLog).filter(AuditLog.action == "booking_code.generated").one()
    assert entry.actor_user_id == user.id
    assert entry.detail["sites"] == ["sportybet_gh"]
    assert entry.detail["status"] == "code_ready"
    assert entry.detail["legs"] == 1


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
