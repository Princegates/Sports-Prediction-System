"""Team news (injuries + confirmed lineups) from API-Football: what it shows,
and -- as much -- what it refrains from spending. The provider is a fake that
counts its calls, because the property under test is that a page view is not a
provider request."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app import team_news
from app.data.providers.api_football import ApiFootballError
from app.db.models import Match, MatchTeamNews, Team
from app.main import app
from app.timeutil import utcnow

client = TestClient(app)
LEAGUE = "English Premier League"


class FakeQuota:
    remaining_reported = None


class FakeClient:
    def __init__(self, injuries=None, lineups=None, fail=False):
        self._injuries = injuries if injuries is not None else INJURIES
        self._lineups = lineups if lineups is not None else LINEUPS
        self.fail = fail
        self.calls: list[str] = []
        self.quota = FakeQuota()

    def injuries(self, fixture_id):
        self.calls.append("injuries")
        if self.fail:
            raise ApiFootballError("HTTP 500")
        return self._injuries

    def lineups(self, fixture_id):
        self.calls.append("lineups")
        if self.fail:
            raise ApiFootballError("HTTP 500")
        return self._lineups


INJURIES = [
    {"player": {"id": 1, "name": "Bukayo Saka", "type": "Missing Fixture", "reason": "Knee Injury"},
     "team": {"id": 42, "name": "Arsenal"}},
    {"player": {"id": 2, "name": "Reece James", "type": "Questionable", "reason": "Hamstring"},
     "team": {"id": 49, "name": "Chelsea"}},
    {"player": {"id": 3, "name": "Nobody FC Player", "type": "Missing Fixture", "reason": "Suspended"},
     "team": {"id": 999, "name": "Some Other Club"}},
    {"player": {"id": 1, "name": "Bukayo Saka", "type": "Missing Fixture", "reason": "Knee Injury"},
     "team": {"id": 42, "name": "Arsenal"}},  # listed twice by the provider
]
LINEUPS = [
    {"team": {"id": 42, "name": "Arsenal"}, "coach": {"name": "M. Arteta"}, "formation": "4-3-3",
     "startXI": [{"player": {"id": 10, "name": "David Raya", "number": 22, "pos": "G"}}],
     "substitutes": [{"player": {"id": 11, "name": "Kai Havertz", "number": 29, "pos": "F"}}]},
    {"team": {"id": 49, "name": "Chelsea"}, "coach": {"name": "E. Maresca"}, "formation": "4-2-3-1",
     "startXI": [{"player": {"id": 20, "name": "Robert Sanchez", "number": 1, "pos": "G"}}],
     "substitutes": []},
]


@pytest.fixture(autouse=True)
def _fresh_module_state():
    team_news._last_failure.clear()
    team_news._reported_remaining = None
    yield
    team_news._last_failure.clear()
    team_news._reported_remaining = None


@pytest.fixture()
def match(db_session):
    home = Team(name="Arsenal", league=LEAGUE, country="England", aliases=[], api_football_team_id=42)
    away = Team(name="Chelsea", league=LEAGUE, country="England", aliases=[], api_football_team_id=49)
    db_session.add_all([home, away])
    db_session.commit()
    row = Match(
        league=LEAGUE, season="2025-26", date=utcnow() + dt.timedelta(hours=30),
        home_team_id=home.id, away_team_id=away.id, status="SCHEDULED", api_fixture_id=777,
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


@pytest.fixture()
def provider(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(team_news, "_client", lambda db: (fake, 1500))
    return fake


# --- injuries -------------------------------------------------------------


def test_injuries_are_split_by_side_and_deduplicated(db_session, match, provider):
    result = team_news.injuries_for(db_session, match)

    assert result["status"] == "ok"
    assert result["home"] == [{"name": "Bukayo Saka", "type": "Missing Fixture", "reason": "Knee Injury"}]
    assert result["away"] == [{"name": "Reece James", "type": "Questionable", "reason": "Hamstring"}]
    # The club that is neither side is dropped, not guessed onto one.
    assert "Nobody FC Player" not in str(result)


def test_a_second_view_is_served_from_the_cache(db_session, match, provider):
    team_news.injuries_for(db_session, match)
    team_news.injuries_for(db_session, match)
    team_news.injuries_for(db_session, match)

    assert provider.calls == ["injuries"]


def test_injuries_go_stale_and_are_refetched(db_session, match, provider):
    now = utcnow()
    team_news.injuries_for(db_session, match, now)
    team_news.injuries_for(db_session, match, now + dt.timedelta(hours=5))  # far from kickoff: still fresh
    assert provider.calls == ["injuries"]

    team_news.injuries_for(db_session, match, now + dt.timedelta(hours=7))
    assert provider.calls == ["injuries", "injuries"]


def test_injuries_refresh_sooner_on_matchday(db_session, match, provider):
    match.date = utcnow() + dt.timedelta(hours=5)
    db_session.commit()
    now = utcnow()
    team_news.injuries_for(db_session, match, now)
    team_news.injuries_for(db_session, match, now + dt.timedelta(minutes=70))

    assert provider.calls == ["injuries", "injuries"]


def test_no_key_means_unavailable_and_no_request(db_session, match, monkeypatch):
    monkeypatch.setattr(team_news, "_client", lambda db: (None, 0))
    result = team_news.injuries_for(db_session, match)

    assert result["status"] == "unavailable" and "key" in result["reason"]


def test_a_match_not_linked_to_the_provider_is_unavailable(db_session, match, provider):
    match.api_fixture_id = None
    db_session.commit()
    result = team_news.injuries_for(db_session, match)

    assert result["status"] == "unavailable"
    assert provider.calls == []


def test_nothing_is_fetched_for_a_finished_match_never_cached(db_session, match, provider):
    match.date = utcnow() - dt.timedelta(days=2)
    db_session.commit()

    assert team_news.injuries_for(db_session, match)["status"] == "unavailable"
    assert provider.calls == []


def test_a_cached_answer_for_a_finished_match_is_still_shown(db_session, match, provider):
    team_news.injuries_for(db_session, match)
    match.date = utcnow() - dt.timedelta(days=2)
    db_session.commit()
    provider.calls.clear()

    result = team_news.injuries_for(db_session, match)

    assert result["status"] == "ok" and provider.calls == []


def test_the_daily_cap_stops_requests_and_serves_the_cache(db_session, match, provider, monkeypatch):
    now = utcnow()
    team_news.injuries_for(db_session, match, now)
    monkeypatch.setattr(team_news, "_client", lambda db: (provider, 1))  # cap of one call a day: spent
    provider.calls.clear()

    stale = team_news.injuries_for(db_session, match, now + dt.timedelta(hours=7))
    assert provider.calls == []
    assert stale["status"] == "ok"  # the old answer, rather than nothing

    other = Match(
        league=LEAGUE, season="2025-26", date=utcnow() + dt.timedelta(hours=30),
        home_team_id=match.home_team_id, away_team_id=match.away_team_id, status="SCHEDULED", api_fixture_id=778,
    )
    db_session.add(other)
    db_session.commit()
    fresh = team_news.injuries_for(db_session, other, now)
    assert fresh["status"] == "unavailable" and "limit" in fresh["reason"]
    assert provider.calls == []


def test_it_backs_off_when_the_provider_reports_little_left(db_session, match, provider):
    team_news._reported_remaining = 100
    result = team_news.injuries_for(db_session, match)

    assert result["status"] == "unavailable"
    assert provider.calls == []


def test_a_failing_provider_is_not_hammered_and_the_failure_is_counted(db_session, match, monkeypatch):
    failing = FakeClient(fail=True)
    monkeypatch.setattr(team_news, "_client", lambda db: (failing, 1500))

    first = team_news.injuries_for(db_session, match)
    second = team_news.injuries_for(db_session, match)
    third = team_news.injuries_for(db_session, match)

    assert first["status"] == second["status"] == third["status"] == "unavailable"
    assert failing.calls == ["injuries"]  # one attempt, then left alone
    assert db_session.query(MatchTeamNews).one().calls_made == 1  # a failed request still costs quota


# --- lineups --------------------------------------------------------------


def test_lineups_are_not_asked_for_until_close_to_kickoff(db_session, match, provider):
    result = team_news.lineups_for(db_session, match)

    assert result["status"] == "pending"
    assert provider.calls == []


def test_confirmed_lineups_are_shown_per_side(db_session, match, provider):
    match.date = utcnow() + dt.timedelta(minutes=45)
    db_session.commit()
    result = team_news.lineups_for(db_session, match)

    assert result["status"] == "ok"
    assert result["home"]["formation"] == "4-3-3" and result["home"]["coach"] == "M. Arteta"
    assert result["home"]["start_xi"] == [{"name": "David Raya", "number": 22, "pos": "G"}]
    assert result["away"]["formation"] == "4-2-3-1"


def test_lineups_not_out_yet_are_reasked_every_five_minutes_not_every_view(db_session, match, monkeypatch):
    empty = FakeClient(lineups=[])
    monkeypatch.setattr(team_news, "_client", lambda db: (empty, 1500))
    match.date = utcnow() + dt.timedelta(minutes=45)
    db_session.commit()
    now = utcnow()

    first = team_news.lineups_for(db_session, match, now)
    team_news.lineups_for(db_session, match, now + dt.timedelta(minutes=2))
    assert first["status"] == "pending" and empty.calls == ["lineups"]

    team_news.lineups_for(db_session, match, now + dt.timedelta(minutes=6))
    assert empty.calls == ["lineups", "lineups"]


def test_lineups_are_kept_for_good_once_the_match_has_started(db_session, match, provider):
    match.date = utcnow() + dt.timedelta(minutes=30)
    db_session.commit()
    now = utcnow()
    team_news.lineups_for(db_session, match, now)
    provider.calls.clear()

    later = team_news.lineups_for(db_session, match, now + dt.timedelta(hours=2))  # long after kickoff

    assert later["status"] == "ok" and provider.calls == []


# --- the endpoints --------------------------------------------------------


def test_the_news_endpoint_returns_team_news(db_session, auth_headers, match, provider):
    body = client.get(f"/api/matches/{match.id}/news", headers=auth_headers).json()

    assert body["status"] == "ok"
    assert body["home"][0]["name"] == "Bukayo Saka"


def test_the_lineup_endpoint_says_when_lineups_are_not_out(db_session, auth_headers, match, provider):
    body = client.get(f"/api/matches/{match.id}/lineup", headers=auth_headers).json()

    assert body["status"] == "pending"


def test_team_news_needs_an_access_grant(db_session, headers_no_access, match, provider):
    assert client.get(f"/api/matches/{match.id}/news", headers=headers_no_access).status_code == 403
    assert provider.calls == []
