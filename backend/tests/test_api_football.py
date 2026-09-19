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


def test_a_moved_kickoff_updates_the_existing_fixture_not_a_duplicate(db_session, clubs):
    """The bug found on live data: a broadcaster moves a kickoff, the
    provider's timestamp no longer matches what is already stored, and an
    exact-timestamp key alone treated that as a brand-new fixture -- every
    club in the league then appeared to "play twice in a day" against the
    audit script, because it genuinely did, on paper."""

    original = Match(
        league="Spanish La Liga", season="2026", date=dt.datetime(2026, 10, 4, 15, 0),
        home_team_id=clubs["Real Madrid CF"].id, away_team_id=clubs["Arsenal FC"].id, status="SCHEDULED",
    )
    db_session.add(original)
    db_session.commit()

    # Same fixture, moved three days later for television -- well inside a
    # normal reschedule, nowhere near a real fixture months away.
    client = FakeClient({
        "fixtures": [{
            "fixture": {"id": 300, "date": "2026-10-07T19:30:00+00:00", "status": {"short": "NS"}},
            "teams": {"home": {"name": "Real Madrid"}, "away": {"name": "Arsenal"}},
            "goals": {"home": None, "away": None},
        }]
    })
    report = import_fixtures(db_session, client, league_id=140, season=2026)

    assert report.inserted == 0
    assert report.rescheduled == 1
    assert db_session.query(Match).count() == 1

    db_session.refresh(original)
    assert original.date == dt.datetime(2026, 10, 7, 19, 30)


def test_a_moved_kickoff_still_brings_its_score_if_already_played(db_session, clubs):
    original = Match(
        league="Spanish La Liga", season="2026", date=dt.datetime(2026, 10, 4, 15, 0),
        home_team_id=clubs["Real Madrid CF"].id, away_team_id=clubs["Arsenal FC"].id, status="SCHEDULED",
    )
    db_session.add(original)
    db_session.commit()

    client = FakeClient({
        "fixtures": [{
            "fixture": {"id": 300, "date": "2026-10-07T19:30:00+00:00", "status": {"short": "FT"}},
            "teams": {"home": {"name": "Real Madrid"}, "away": {"name": "Arsenal"}},
            "goals": {"home": 3, "away": 1},
        }]
    })
    import_fixtures(db_session, client, league_id=140, season=2026)

    db_session.refresh(original)
    assert (original.status, original.home_score, original.away_score) == ("FINISHED", 3, 1)


def test_a_kickoff_far_outside_the_window_is_a_new_fixture_not_a_reschedule(db_session, clubs):
    """A genuine second meeting -- the reverse fixture, months later -- must
    never be folded into the first one just because it shares both clubs."""

    original = Match(
        league="Spanish La Liga", season="2026", date=dt.datetime(2026, 10, 4, 15, 0),
        home_team_id=clubs["Real Madrid CF"].id, away_team_id=clubs["Arsenal FC"].id, status="SCHEDULED",
    )
    db_session.add(original)
    db_session.commit()

    client = FakeClient({
        "fixtures": [{
            "fixture": {"id": 301, "date": "2027-02-20T19:30:00+00:00", "status": {"short": "NS"}},
            "teams": {"home": {"name": "Real Madrid"}, "away": {"name": "Arsenal"}},
            "goals": {"home": None, "away": None},
        }]
    })
    report = import_fixtures(db_session, client, league_id=140, season=2026)

    assert report.inserted == 1
    assert report.rescheduled == 0
    assert db_session.query(Match).count() == 2


def test_a_finished_fixtures_date_is_never_rewritten_by_a_reschedule_match(db_session, clubs):
    """Only a still-SCHEDULED fixture can be a reschedule target -- a
    FINISHED match's date is a fact of history, not something still moving,
    and folding a later provider row onto it would corrupt Elo's chronology."""

    finished = Match(
        league="Spanish La Liga", season="2026", date=dt.datetime(2026, 10, 4, 15, 0),
        home_team_id=clubs["Real Madrid CF"].id, away_team_id=clubs["Arsenal FC"].id,
        status="FINISHED", home_score=2, away_score=0,
    )
    db_session.add(finished)
    db_session.commit()

    client = FakeClient({
        "fixtures": [{
            "fixture": {"id": 302, "date": "2026-10-06T19:30:00+00:00", "status": {"short": "NS"}},
            "teams": {"home": {"name": "Real Madrid"}, "away": {"name": "Arsenal"}},
            "goals": {"home": None, "away": None},
        }]
    })
    report = import_fixtures(db_session, client, league_id=140, season=2026)

    assert report.inserted == 1  # a new SCHEDULED row -- a genuine next meeting
    db_session.refresh(finished)
    assert finished.date == dt.datetime(2026, 10, 4, 15, 0)  # untouched


def test_a_domestic_leagues_first_import_creates_its_clubs(db_session):
    """The opposite case from a European tie: a brand-new domestic league has
    no existing rows to resolve to. Skipping every fixture until someone
    seeds the clubs by hand would mean the league could never be imported at
    all, so it creates them instead -- the same as the free providers do."""

    before = db_session.query(Team).count()
    client = FakeClient({
        "fixtures": [{
            "fixture": {"id": 200, "date": "2026-10-01T19:00:00+00:00", "status": {"short": "NS"}},
            "teams": {"home": {"name": "Galatasaray"}, "away": {"name": "Fenerbahçe"}},
            "goals": {"home": None, "away": None},
        }]
    })

    report = import_fixtures(db_session, client, league_id=203, season=2026)

    assert report.inserted == 1
    assert db_session.query(Team).count() == before + 2

    match = db_session.query(Match).filter_by(league="Turkish Süper Lig").one()
    home = db_session.get(Team, match.home_team_id)
    away = db_session.get(Team, match.away_team_id)
    assert {home.name, away.name} == {"Galatasaray", "Fenerbahçe"}
    assert home.league == away.league == "Turkish Süper Lig"


def test_a_club_seen_twice_in_one_domestic_import_is_created_once(db_session):
    client = FakeClient({
        "fixtures": [
            {
                "fixture": {"id": 201, "date": "2026-10-01T19:00:00+00:00", "status": {"short": "NS"}},
                "teams": {"home": {"name": "Galatasaray"}, "away": {"name": "Fenerbahçe"}},
                "goals": {"home": None, "away": None},
            },
            {
                "fixture": {"id": 202, "date": "2026-10-08T19:00:00+00:00", "status": {"short": "NS"}},
                "teams": {"home": {"name": "Fenerbahçe"}, "away": {"name": "Galatasaray"}},
                "goals": {"home": None, "away": None},
            },
        ]
    })

    report = import_fixtures(db_session, client, league_id=203, season=2026)

    assert report.inserted == 2
    assert db_session.query(Team).filter_by(league="Turkish Süper Lig").count() == 2


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


def test_odds_attach_by_fixture_id_with_no_teams_block_at_all(db_session, clubs):
    """The real /odds response, confirmed against a live call: no ``teams``
    key anywhere in a fixture row, only ``fixture.id``. Team-name matching
    here was an unverified assumption and silently dropped every row --
    this pins the shape that actually comes back."""

    kickoff = "2026-10-01T19:00:00+00:00"
    fixtures = FakeClient({
        "fixtures": [{
            "fixture": {"id": 555, "date": kickoff, "status": {"short": "NS"}},
            "teams": {"home": {"name": "Real Madrid"}, "away": {"name": "Bayern Munich"}},
            "goals": {"home": None, "away": None},
        }]
    })
    import_fixtures(db_session, fixtures, league_id=2, season=2026)

    odds_client = FakeClient({
        "odds": [{
            "fixture": {"id": 555, "date": kickoff},
            "league": {"id": 2, "name": "UEFA Champions League"},
            "bookmakers": [{
                "name": "William Hill",
                "bets": [{"name": "Match Winner", "values": [
                    {"value": "Home", "odd": "2.10"}, {"value": "Draw", "odd": "3.40"}, {"value": "Away", "odd": "3.60"},
                ]}],
            }],
        }]
    })
    report = import_odds(db_session, odds_client, league_id=2, season=2026)

    assert report.inserted == 3
    assert {r.selection for r in db_session.query(MatchOdds).all()} == {"Home Win", "Draw", "Away Win"}


def test_odds_ignore_team_names_entirely_and_match_only_on_fixture_id(db_session, clubs):
    """Even when a ``teams`` block is present, it must play no role --
    proven by giving it names that don't exist anywhere and confirming the
    price still attaches, purely on the shared fixture id."""

    kickoff = "2026-10-01T19:00:00+00:00"
    fixtures = FakeClient({
        "fixtures": [{
            "fixture": {"id": 777, "date": kickoff, "status": {"short": "NS"}},
            "teams": {"home": {"name": "Real Madrid"}, "away": {"name": "Bayern Munich"}},
            "goals": {"home": None, "away": None},
        }]
    })
    import_fixtures(db_session, fixtures, league_id=2, season=2026)

    odds_client = FakeClient({
        "odds": [{
            "fixture": {"id": 777, "date": kickoff},
            "teams": {"home": {"name": "Nonexistent United"}, "away": {"name": "Not A Real Club FC"}},
            "bookmakers": [{"name": "Bet365", "bets": [{"name": "Match Winner",
                            "values": [{"value": "Home", "odd": "1.90"}]}]}],
        }]
    })
    report = import_odds(db_session, odds_client, league_id=2, season=2026)

    assert report.inserted == 1


def test_a_fixture_id_not_yet_imported_is_dropped(db_session, clubs):
    """Odds for a fixture this league's import hasn't seen yet -- not the
    "wrong league" case, just not stored -- attach to nothing."""

    client = FakeClient({
        "odds": [{
            "fixture": {"id": 999, "date": "2026-10-05T19:00:00+00:00"},
            "bookmakers": [{"name": "Bet365", "bets": [{"name": "Match Winner",
                            "values": [{"value": "Home", "odd": "2.0"}]}]}],
        }]
    })
    report = import_odds(db_session, client, league_id=2, season=2026)
    assert report.inserted == 0


def test_import_fixtures_stores_the_provider_fixture_id(db_session, clubs):
    kickoff = "2026-10-01T19:00:00+00:00"
    fixtures = FakeClient({
        "fixtures": [{
            "fixture": {"id": 4242, "date": kickoff, "status": {"short": "NS"}},
            "teams": {"home": {"name": "Real Madrid"}, "away": {"name": "Bayern Munich"}},
            "goals": {"home": None, "away": None},
        }]
    })
    import_fixtures(db_session, fixtures, league_id=2, season=2026)

    match = db_session.query(Match).one()
    assert match.api_fixture_id == 4242


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


def _run_with_odds(module, monkeypatch, db_session, *, days_ahead, odds):
    monkeypatch.setattr(sys, "argv", [
        "import_api_football.py", "--leagues", "UEFA Champions League",
        "--odds", "--days-ahead", str(days_ahead),
    ])
    monkeypatch.setattr(module.app_settings, "all_values", lambda db: {"api_football_key": "test-key"})
    monkeypatch.setattr(module, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(module, "init_db", lambda engine: None)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(module, "import_fixtures", lambda db, client, *, league_id, season: ImportReport())
    monkeypatch.setattr(module, "import_odds", odds)


def test_odds_capture_asks_once_per_day_in_the_window(import_script, monkeypatch, db_session, capsys):
    """The bug this replaces: a single unscoped league+season request, which
    only ever reads page 1 of however a whole season's /odds response is
    ordered -- not "the next few days", the only window a booking code ever
    needs a price for."""

    calls: list = []

    def fake_odds(db, client, *, league_id, season, date):
        calls.append(date)
        return ImportReport(considered=1, inserted=1)

    _run_with_odds(import_script, monkeypatch, db_session, days_ahead=3, odds=fake_odds)
    import_script.main()

    assert len(calls) == 3
    assert calls == sorted(set(calls))  # three distinct, ascending dates
    assert (calls[-1] - calls[0]).days == 2

    out = capsys.readouterr().out
    assert "3 price(s) stored across the next 3 day(s)" in out


def test_odds_capture_stops_the_run_when_quota_runs_out_mid_window(import_script, monkeypatch, db_session, capsys):
    calls: list = []

    def fake_odds(db, client, *, league_id, season, date):
        calls.append(date)
        if len(calls) == 2:
            raise QuotaExceeded("budget spent")
        return ImportReport(considered=1, inserted=1)

    _run_with_odds(import_script, monkeypatch, db_session, days_ahead=5, odds=fake_odds)

    with pytest.raises(SystemExit) as exit_info:
        import_script.main()

    assert exit_info.value.code == 1
    assert len(calls) == 2  # stopped mid-window, not all 5 days attempted
    err = capsys.readouterr().err
    assert "odds stopped after 1/5 day(s)" in err


# --- ambiguity ---------------------------------------------------------


def _store(db, *names_and_leagues):
    made = []
    for name, league in names_and_leagues:
        team = Team(name=name, league=league, aliases=[])
        db.add(team)
        made.append(team)
    db.commit()
    return made


@pytest.mark.parametrize("order", ["real first", "atletico first"])
def test_madrid_resolves_by_evidence_not_row_order(db_session, order):
    """Both Madrid clubs score a perfect 1.00 for "Atletico Madrid".

    "Real Madrid CF" reduces to the single token "madrid" once "Real" and
    "CF" are stripped, and "Atletico Madrid" accounts for all of it. Ranking
    on that number alone, the winner was whichever row the loop reached
    first -- so Atletico's Champions League ties landed in Real Madrid's
    history or not depending on primary-key order, silently.
    """

    rows = [("Real Madrid CF", "Spanish La Liga"), ("Atletico Madrid", "Spanish La Liga")]
    if order == "atletico first":
        rows.reverse()
    _store(db_session, *rows)

    resolved = TeamIndex(db_session).resolve("Atlético Madrid")
    assert resolved is not None and resolved.name == "Atletico Madrid"


@pytest.mark.parametrize("order", ["villa first", "villarreal first"])
def test_villarreal_is_not_aston_villa(db_session, order):
    """The one that actually happened, found by the audit on live data.

    Abbreviations are matched by prefix -- "man" for "manchester", "wolv" for
    "wolverhampton" -- and "villa" is a prefix of "villarreal". So Villarreal
    scored a perfect match against Aston Villa, and the Spanish club's
    Champions League ties were filed under an English one. The audit caught it
    as Aston Villa playing twice on 3 November and meeting PSG twice in a
    league phase that pairs clubs once.
    """

    rows = [("Aston Villa FC", "English Premier League"), ("Villarreal CF", "Spanish La Liga")]
    if order == "villarreal first":
        rows.reverse()
    _store(db_session, *rows)

    resolved = TeamIndex(db_session).resolve("Villarreal")
    assert resolved is not None and resolved.name == "Villarreal CF"


def test_a_name_that_fits_two_clubs_equally_is_refused(db_session):
    """"Sporting" alone is Gijón and Lisbon with equal force. Picking either
    attaches a tie to a club that did not play it, which looks entirely
    normal afterwards -- so nothing is picked."""

    _store(
        db_session,
        ("Sporting Gijon", "Spanish La Liga"),
        ("Sporting Lisbon", "Portuguese Primeira Liga"),
    )

    assert TeamIndex(db_session).resolve("Sporting") is None


def test_a_promoted_clubs_old_division_row_no_longer_blocks_its_new_one(db_session):
    """The bug this was found for: a club keeps its old division's Team row
    (a real, separate history) alongside a new one for wherever it plays
    now, so its own exact name is tied against itself across two leagues.
    A caller resolving fixtures for one specific domestic league already
    knows every club in them plays there -- that is not a guess, it is
    the one piece of context a name score alone never has."""

    lower, upper = _store(
        db_session,
        ("AFC Bournemouth", "English Championship"),
        ("AFC Bournemouth", "English Premier League"),
    )

    index = TeamIndex(db_session)
    assert index.resolve("Bournemouth") is None  # unchanged without that context
    assert index.resolve("Bournemouth", prefer_league="English Premier League") is upper
    assert index.resolve("Bournemouth", prefer_league="English Championship") is lower


def test_prefer_league_settles_a_cross_league_tie_correctly_too(db_session):
    """Sporting Gijon and Sporting Lisbon are two different clubs, not one
    club in two divisions -- but a caller resolving names for a Spanish La
    Liga fixture list still knows, as a hard fact rather than a guess, that
    every club in it plays in Spanish La Liga. That is exactly the same
    reasoning the promoted-club case relies on, so it resolves the same
    way: to whichever tied candidate is actually in that league."""

    gijon, _lisbon = _store(
        db_session,
        ("Sporting Gijon", "Spanish La Liga"),
        ("Sporting Lisbon", "Portuguese Primeira Liga"),
    )

    assert TeamIndex(db_session).resolve("Sporting", prefer_league="Spanish La Liga") is gijon


def test_prefer_league_does_not_rescue_a_tie_within_the_same_league(db_session):
    """Narrowing by league still leaves more than one candidate when two
    different, similarly-named clubs share the target league itself --
    that is a real ambiguity prefer_league cannot and must not resolve.
    Same tie as the cross-league case above, moved into one league to
    isolate exactly what prefer_league does and does not settle."""

    _store(
        db_session,
        ("Sporting Gijon", "Spanish La Liga"),
        ("Sporting Lisbon", "Spanish La Liga"),
    )

    assert TeamIndex(db_session).resolve("Sporting", prefer_league="Spanish La Liga") is None


def test_both_unmatched_clubs_are_reported_not_just_the_home_side(db_session):
    """A qualifying tie between two clubs from leagues we do not hold used to
    report only one of them, so the other never appeared in the list a human
    reads to decide whether an alias is missing."""

    report = ImportReport()
    index = TeamIndex(db_session)
    for name in ("Tre Fiori", "Larne"):
        candidate, score = index.nearest(name)
        report.note_unresolved(name, candidate.name if candidate else None, score)

    assert sorted(report.unresolved_clubs) == ["Larne", "Tre Fiori"]


# --- BTTS and Over/Under prices, named to match the outcome registry --------


def test_btts_prices_are_captured(db_session, clubs):
    kickoff = "2026-10-01T19:00:00+00:00"
    fixtures = FakeClient({
        "fixtures": [{
            "fixture": {"id": 5, "date": kickoff, "status": {"short": "NS"}},
            "teams": {"home": {"name": "Real Madrid"}, "away": {"name": "Bayern Munich"}},
            "goals": {"home": None, "away": None},
        }]
    })
    import_fixtures(db_session, fixtures, league_id=2, season=2026)

    odds_client = FakeClient({
        "odds": [{
            "fixture": {"id": 5, "date": kickoff},
            "teams": {"home": {"name": "Real Madrid"}, "away": {"name": "Bayern Munich"}},
            "bookmakers": [{
                "name": "Bet365",
                "bets": [{
                    "name": "Both Teams Score",
                    "values": [{"value": "Yes", "odd": "1.65"}, {"value": "No", "odd": "2.20"}],
                }],
            }],
        }]
    })
    import_odds(db_session, odds_client, league_id=2, season=2026)

    rows = db_session.query(MatchOdds).all()
    assert {(r.market, r.selection) for r in rows} == {
        ("Both Teams To Score", "Yes"),
        ("Both Teams To Score", "No"),
    }


def test_over_under_prices_are_captured_per_line(db_session, clubs):
    kickoff = "2026-10-01T19:00:00+00:00"
    fixtures = FakeClient({
        "fixtures": [{
            "fixture": {"id": 6, "date": kickoff, "status": {"short": "NS"}},
            "teams": {"home": {"name": "Real Madrid"}, "away": {"name": "Bayern Munich"}},
            "goals": {"home": None, "away": None},
        }]
    })
    import_fixtures(db_session, fixtures, league_id=2, season=2026)

    odds_client = FakeClient({
        "odds": [{
            "fixture": {"id": 6, "date": kickoff},
            "teams": {"home": {"name": "Real Madrid"}, "away": {"name": "Bayern Munich"}},
            "bookmakers": [{
                "name": "Bet365",
                "bets": [{
                    "name": "Goals Over/Under",
                    "values": [
                        {"value": "Over 1.5", "odd": "1.25"},
                        {"value": "Under 1.5", "odd": "3.75"},
                        {"value": "Over 2.5", "odd": "1.90"},
                        {"value": "Under 2.5", "odd": "1.90"},
                    ],
                }],
            }],
        }]
    })
    import_odds(db_session, odds_client, league_id=2, season=2026)

    rows = db_session.query(MatchOdds).all()
    assert {(r.market, r.selection) for r in rows} == {
        ("Total Goals 1.5", "Over 1.5"),
        ("Total Goals 1.5", "Under 1.5"),
        ("Total Goals 2.5", "Over 2.5"),
        ("Total Goals 2.5", "Under 2.5"),
    }


def test_an_unpriced_bet_type_is_skipped_not_guessed(db_session, clubs):
    """A bet this project has no market for (corners, cards, ...) is dropped
    rather than stored under an invented name."""

    kickoff = "2026-10-01T19:00:00+00:00"
    fixtures = FakeClient({
        "fixtures": [{
            "fixture": {"id": 7, "date": kickoff, "status": {"short": "NS"}},
            "teams": {"home": {"name": "Real Madrid"}, "away": {"name": "Bayern Munich"}},
            "goals": {"home": None, "away": None},
        }]
    })
    import_fixtures(db_session, fixtures, league_id=2, season=2026)

    odds_client = FakeClient({
        "odds": [{
            "fixture": {"id": 7, "date": kickoff},
            "teams": {"home": {"name": "Real Madrid"}, "away": {"name": "Bayern Munich"}},
            "bookmakers": [{
                "name": "Bet365",
                "bets": [{"name": "Corners Over/Under", "values": [{"value": "Over 9.5", "odd": "1.90"}]}],
            }],
        }]
    })
    report = import_odds(db_session, odds_client, league_id=2, season=2026)

    assert report.inserted == 0
    assert db_session.query(MatchOdds).count() == 0


def test_a_bare_numeric_value_does_not_crash_the_import(db_session, clubs):
    """Found against a live response: this project only recognises three
    markets, but _parse_bet() runs for every bet a bookmaker offers, and
    some unrelated markets (handicap lines, corner counts) hand back a
    bare number rather than a string -- '2.5' the float, not "Over 2.5".
    Before this crashed the whole run on .strip(); it must instead just
    fail to match one of this project's three recognised markets, same as
    any other bet type it doesn't price."""

    kickoff = "2026-10-01T19:00:00+00:00"
    fixtures = FakeClient({
        "fixtures": [{
            "fixture": {"id": 42, "date": kickoff, "status": {"short": "NS"}},
            "teams": {"home": {"name": "Real Madrid"}, "away": {"name": "Bayern Munich"}},
            "goals": {"home": None, "away": None},
        }]
    })
    import_fixtures(db_session, fixtures, league_id=2, season=2026)

    odds_client = FakeClient({
        "odds": [{
            "fixture": {"id": 42, "date": kickoff},
            "bookmakers": [{
                "name": "Pinnacle",
                "bets": [
                    {"name": "Asian Handicap", "values": [{"value": 2.5, "odd": "1.90"}]},
                    {"name": "Match Winner", "values": [{"value": "Home", "odd": "2.10"}]},
                ],
            }],
        }]
    })
    report = import_odds(db_session, odds_client, league_id=2, season=2026)

    assert report.inserted == 1
    assert db_session.query(MatchOdds).one().selection == "Home Win"


def test_captured_market_names_match_the_outcome_registry_exactly():
    """The whole point of naming odds this way: a booking-code leg is built
    by joining a model outcome to a stored price on (market, selection). If
    the two sides ever drifted apart that join would silently return
    nothing. This pins both sides against the same real bet-provider shapes,
    so a rename on either side breaks a test instead of breaking silently in
    production."""

    from app.data.api_football_ingest import _parse_bet
    from app.outcomes.registry import build_outcome_registry, matrix_derived_outcomes

    outcomes = build_outcome_registry(
        home_win=0.4, draw=0.3, away_win=0.3,
        over_probabilities={"1.5": 0.8, "2.5": 0.55},
        btts_yes=0.6, btts_no=0.4,
        correct_score_probabilities={"1-0": 0.12, "0-0": 0.08},
        matches_available=10,
    )
    # Draw No Bet is matrix-derived (app.outcomes.registry.matrix_derived_outcomes),
    # not part of build_outcome_registry() itself.
    outcomes += matrix_derived_outcomes(lambda_home=1.4, lambda_away=1.1, matches_available=10)
    registry_pairs = {(o.market, o.selection) for o in outcomes}

    provider_pairs = set()
    for bet_name, value in [
        ("Match Winner", "Home"), ("Match Winner", "Draw"), ("Match Winner", "Away"),
        ("Both Teams Score", "Yes"), ("Both Teams Score", "No"),
        ("Goals Over/Under", "Over 1.5"), ("Goals Over/Under", "Under 1.5"),
        ("Goals Over/Under", "Over 2.5"), ("Goals Over/Under", "Under 2.5"),
        ("Home/Away", "Home"), ("Home/Away", "Away"),
        ("Exact Score", "1:0"), ("Exact Score", "0:0"),
    ]:
        parsed = _parse_bet(bet_name, value)
        assert parsed is not None, f"{bet_name}/{value} produced no market"
        provider_pairs.add(parsed)

    assert provider_pairs <= registry_pairs, provider_pairs - registry_pairs


def test_home_away_bet_maps_to_draw_no_bet():
    """API-Football's real name for this bet is literally "Home/Away" -- two
    values, stake refunded on a draw, which is Draw No Bet by definition.
    The registry's own selection text for it is already "Home"/"Away", so
    this is a market-name rename only, no value translation."""

    from app.data.api_football_ingest import _parse_bet

    assert _parse_bet("Home/Away", "Home") == ("Draw No Bet", "Home")
    assert _parse_bet("Home/Away", "Away") == ("Draw No Bet", "Away")
    assert _parse_bet("Home/Away", "Draw") is None


def test_exact_score_bet_maps_to_correct_score_with_dash_separator():
    """Confirmed against a live response: the provider spells a scoreline
    "1:0"; app.prediction_models.poisson_model spells the same scoreline
    "1-0" (f"{h}-{a}"). Only the separator needs converting."""

    from app.data.api_football_ingest import _parse_bet

    assert _parse_bet("Exact Score", "1:0") == ("Correct Score", "1-0")
    assert _parse_bet("Exact Score", "0:0") == ("Correct Score", "0-0")
    assert _parse_bet("Exact Score", "12:3") == ("Correct Score", "12-3")
    assert _parse_bet("Exact Score", "not a score") is None
