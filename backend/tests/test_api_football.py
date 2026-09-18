"""API-Football: budget discipline, odds maths, and club identity.

Three things are worth testing here and they are not the happy path.

The **budget** is a hundred requests a day. Code that spends it carelessly
fails at 3pm on a Saturday, so the guard has to refuse before the request, not
after the API does.

The **overround** is the difference between a real edge and an imaginary one.
Comparing a model probability against raw implied odds credits the model with
an edge on every selection, because the bookmaker's margin is counted as the
model's insight.

**Club identity** is the one that would do lasting damage. A Team row is
unique on (name, league), so a European tie imported carelessly creates a
second Real Madrid with no history, and predictions built on it look entirely
normal while being worthless.
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest

from app.data.api_football_ingest import ImportReport, TeamIndex, import_fixtures, import_odds
from app.data.providers.api_football import ApiFootballClient, ApiFootballError, QuotaExceeded
from app.db.models import Match, MatchOdds, Team
from app.odds import MarketPrice, assess, fair_probabilities, overround


class FakeClient(ApiFootballClient):
    """Real quota logic, scripted responses -- the network is the only part
    replaced, so the budget accounting under test is the shipping one."""

    def __init__(self, payloads: dict[str, list[dict]], **kwargs):
        super().__init__("test-key", **kwargs)
        self.payloads = payloads
        self.calls: list[tuple[str, dict]] = []

    def get(self, path: str, params: dict | None = None) -> list[dict]:
        if self.quota.remaining() <= 0:
            raise QuotaExceeded("budget spent")
        self.calls.append((path, params or {}))
        self.quota.used_this_run += 1
        return self.payloads.get(path, [])


# --- budget -----------------------------------------------------------


def test_quota_refuses_before_spending_a_request():
    client = ApiFootballClient("k", daily_budget=3)
    client.quota.used_this_run = 3
    with pytest.raises(QuotaExceeded):
        client.get("status")
    # Nothing was attempted, so nothing extra was counted.
    assert client.quota.used_this_run == 3


def test_the_apis_own_remaining_count_wins_when_lower():
    """Another process may share the key, so our count can be optimistic."""

    client = ApiFootballClient("k", daily_budget=100)
    client.quota.used_this_run = 10
    assert client.quota.remaining() == 90
    client.quota.remaining_reported = 4
    assert client.quota.remaining() == 4


def test_auth_header_follows_the_host():
    assert "x-apisports-key" in ApiFootballClient("k")._headers()
    rapid = ApiFootballClient("k", host="api-football-v1.p.rapidapi.com")
    assert "x-rapidapi-key" in rapid._headers()
    assert "x-rapidapi-host" in rapid._headers()


# --- odds maths -------------------------------------------------------


def test_margin_is_removed_before_comparing():
    prices = [MarketPrice("Home Win", 2.0), MarketPrice("Draw", 3.5), MarketPrice("Away Win", 4.0)]
    assert overround(prices) > 1.0
    fair = fair_probabilities(prices)
    assert abs(sum(fair.values()) - 1.0) < 1e-9


def test_agreeing_with_the_market_is_not_an_edge():
    """The test that matters. Feed the model exactly the market's own fair
    probabilities: every edge must be zero. Skip the overround and each one
    comes out positive, which would read as an edge on every selection."""

    prices = [MarketPrice("Home Win", 2.0), MarketPrice("Draw", 3.5), MarketPrice("Away Win", 4.0)]
    fair = fair_probabilities(prices)

    for assessment in assess(fair, prices):
        assert abs(assessment.edge) < 1e-9, f"{assessment.selection} showed an edge against its own price"

    raw_implied = {p.selection: p.raw_implied for p in prices}
    naive = [a.edge for a in assess(raw_implied, prices)]
    assert all(e > 0 for e in naive), "this is the mistake the overround removal prevents"


def test_expected_value_matches_the_definition():
    prices = [MarketPrice("Home Win", 2.0)]
    [result] = assess({"Home Win": 0.55}, prices)
    assert result.expected_value == pytest.approx(0.55 * 2.0 - 1.0)


def test_selections_without_both_sides_are_dropped():
    prices = [MarketPrice("Home Win", 2.0)]
    assert [a.selection for a in assess({"Home Win": 0.5, "Draw": 0.3}, prices)] == ["Home Win"]


# --- club identity ----------------------------------------------------


@pytest.fixture()
def clubs(db_session):
    made = {}
    for name, league in [
        ("Real Madrid CF", "Spanish La Liga"),
        ("FC Bayern München", "German Bundesliga"),
        ("Arsenal FC", "English Premier League"),
    ]:
        team = Team(name=name, league=league, aliases=[])
        db_session.add(team)
        made[name] = team
    db_session.commit()
    for t in made.values():
        db_session.refresh(t)
    return made


def test_index_resolves_a_club_across_leagues(db_session, clubs):
    index = TeamIndex(db_session)
    # The provider's spelling differs from ours; the club is still the club.
    assert index.resolve("Real Madrid").id == clubs["Real Madrid CF"].id
    assert index.resolve("Bayern Munich").id == clubs["FC Bayern München"].id
    assert index.resolve("Arsenal").id == clubs["Arsenal FC"].id


def test_european_fixture_reuses_domestic_clubs_rather_than_duplicating(db_session, clubs):
    """The whole point. A Champions League tie must attach to the La Liga Real
    Madrid, with its history, not mint a second one starting from nothing."""

    before = db_session.query(Team).count()
    client = FakeClient({
        "fixtures": [{
            "fixture": {"id": 99, "date": "2026-10-01T19:00:00+00:00", "status": {"short": "NS"}},
            "teams": {"home": {"name": "Real Madrid"}, "away": {"name": "Bayern Munich"}},
            "goals": {"home": None, "away": None},
        }]
    })

    report = import_fixtures(db_session, client, league_id=2, season=2026)

    assert report.inserted == 1
    assert db_session.query(Team).count() == before, "a duplicate club row was created"

    match = db_session.query(Match).filter_by(league="UEFA Champions League").one()
    assert match.home_team_id == clubs["Real Madrid CF"].id
    assert match.away_team_id == clubs["FC Bayern München"].id


def test_an_unrecognised_club_is_skipped_not_invented(db_session, clubs):
    client = FakeClient({
        "fixtures": [{
            "fixture": {"id": 100, "date": "2026-10-01T19:00:00+00:00", "status": {"short": "NS"}},
            "teams": {"home": {"name": "Real Madrid"}, "away": {"name": "Shakhtar Donetsk"}},
            "goals": {"home": None, "away": None},
        }]
    })

    report = import_fixtures(db_session, client, league_id=2, season=2026)

    assert report.inserted == 0
    assert report.skipped_unresolved and "Shakhtar" in report.skipped_unresolved[0]
    assert db_session.query(Match).count() == 0
    assert db_session.query(Team).count() == len(clubs)


def test_finished_fixtures_bring_their_scores(db_session, clubs):
    client = FakeClient({
        "fixtures": [{
            "fixture": {"id": 101, "date": "2026-09-01T19:00:00+00:00", "status": {"short": "FT"}},
            "teams": {"home": {"name": "Real Madrid"}, "away": {"name": "Arsenal"}},
            "goals": {"home": 2, "away": 1},
        }]
    })
    import_fixtures(db_session, client, league_id=2, season=2026)

    match = db_session.query(Match).one()
    assert (match.status, match.home_score, match.away_score) == ("FINISHED", 2, 1)


# --- odds capture -----------------------------------------------------


def test_odds_attach_to_a_stored_match(db_session, clubs):
    kickoff = "2026-10-01T19:00:00+00:00"
    fixtures = FakeClient({
        "fixtures": [{
            "fixture": {"id": 99, "date": kickoff, "status": {"short": "NS"}},
            "teams": {"home": {"name": "Real Madrid"}, "away": {"name": "Bayern Munich"}},
            "goals": {"home": None, "away": None},
        }]
    })
    import_fixtures(db_session, fixtures, league_id=2, season=2026)

    odds_client = FakeClient({
        "odds": [{
            "fixture": {"id": 99, "date": kickoff},
            "teams": {"home": {"name": "Real Madrid"}, "away": {"name": "Bayern Munich"}},
            "bookmakers": [{
                "name": "Bet365",
                "bets": [{
                    "name": "Match Winner",
                    "values": [
                        {"value": "Home", "odd": "2.10"},
                        {"value": "Draw", "odd": "3.40"},
                        {"value": "Away", "odd": "3.60"},
                    ],
                }],
            }],
        }]
    })
    report = import_odds(db_session, odds_client, league_id=2, season=2026)

    assert report.inserted == 3
    rows = db_session.query(MatchOdds).all()
    assert {r.selection for r in rows} == {"Home Win", "Draw", "Away Win"}
    assert all(r.market == "Match Result" and r.bookmaker == "Bet365" for r in rows)
    # Raw, exactly as published -- the margin comes off at read time.
    assert sorted(r.decimal_odds for r in rows) == [2.10, 3.40, 3.60]


def test_odds_for_an_unknown_match_are_dropped(db_session, clubs):
    """Prices with nothing to attach to are not data, they are a future join
    waiting to go wrong."""

    client = FakeClient({
        "odds": [{
            "fixture": {"id": 7, "date": "2026-10-05T19:00:00+00:00"},
            "teams": {"home": {"name": "Real Madrid"}, "away": {"name": "Bayern Munich"}},
            "bookmakers": [{"name": "Bet365", "bets": [{"name": "Match Winner",
                            "values": [{"value": "Home", "odd": "2.0"}]}]}],
        }]
    })
    report = import_odds(db_session, client, league_id=2, season=2026)
    assert report.inserted == 0
    assert db_session.query(MatchOdds).count() == 0


# --- the run's exit code ----------------------------------------------


@pytest.fixture()
def import_script():
    """Loads scripts/import_api_football.py as a module.

    It is a script, not a package member, so there is no import path to it --
    the same spec-loading the compile guard uses."""

    import importlib.util

    path = Path(__file__).resolve().parents[1] / "scripts" / "import_api_football.py"
    spec = importlib.util.spec_from_file_location("import_api_football_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(module, monkeypatch, db_session, *, fixtures):
    """Runs the script's main() against a stubbed importer.

    Everything the run needs from outside is replaced: the settings lookup
    supplies the key, and ``fixtures`` stands in for the network call.
    """

    monkeypatch.setattr(sys, "argv", ["import_api_football.py", "--leagues", "UEFA Champions League"])
    monkeypatch.setattr(module.app_settings, "all_values", lambda db: {"api_football_key": "test-key"})
    monkeypatch.setattr(module, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(module, "init_db", lambda engine: None)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(module, "import_fixtures", fixtures)
    return module.main()


def test_a_run_that_imported_nothing_fails(import_script, monkeypatch, db_session, capsys):
    """The failure this was written for: every request refused, every error
    caught, and the job still reported success. A green tick over an empty
    import is worse than a red one -- it is believed."""

    def refuse(db, client, *, league_id, season):
        raise ApiFootballError({"plan": "Free plans do not have access to this season."})

    with pytest.raises(SystemExit) as exit_info:
        _run(import_script, monkeypatch, db_session, fixtures=refuse)

    assert exit_info.value.code == 1
    err = capsys.readouterr().err
    assert "Did not import: UEFA Champions League" in err


def test_a_run_that_imported_something_succeeds(import_script, monkeypatch, db_session, capsys):
    def succeed(db, client, *, league_id, season):
        return ImportReport(considered=4, inserted=4, updated=0, skipped_unresolved=[])

    _run(import_script, monkeypatch, db_session, fixtures=succeed)

    out = capsys.readouterr().out
    assert "4 fixtures added" in out
