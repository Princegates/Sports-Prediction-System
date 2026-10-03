"""Squad data ingestion: parsing API-Football's lineups/injuries responses
into this project's own tables.

Team identity reuses TeamIndex's fuzzy name matching (see test_api_football.py
for why that's the hard part generally); the one new wrinkle here is lineups,
where the two team blocks in a response are matched against *this specific
match's* own two teams rather than resolved against the whole league --
tested separately below since a bad match there silently attributes a
player's absence to the wrong side of the fixture.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app import app_settings
from app.data import squad_ingest
from app.data.providers.api_football import ApiFootballClient, QuotaExceeded
from app.data.squad_ingest import (
    import_injuries,
    import_injuries_from_settings,
    import_lineup,
    run_lineup_check_from_settings,
)
from app.db.models import Match, MatchLineup, Player, PlayerAbsence, Team

LEAGUE = "English Premier League"
BASE = dt.datetime(2026, 1, 1, 15, 0)


class FakeClient(ApiFootballClient):
    """Same shape as test_api_football.py's own FakeClient: real quota
    accounting, scripted responses, so request-cost behavior is still under
    test even though the network is replaced."""

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


@pytest.fixture()
def clubs(db_session):
    home = Team(name="Arsenal FC", league=LEAGUE, aliases=[])
    away = Team(name="Chelsea FC", league=LEAGUE, aliases=[])
    db_session.add_all([home, away])
    db_session.commit()
    db_session.refresh(home)
    db_session.refresh(away)
    return home, away


@pytest.fixture()
def fixture_match(db_session, clubs):
    home, away = clubs
    match = Match(
        league=LEAGUE,
        season="2025-26",
        date=BASE + dt.timedelta(days=1),
        home_team_id=home.id,
        away_team_id=away.id,
        status="SCHEDULED",
        api_fixture_id=555,
    )
    db_session.add(match)
    db_session.commit()
    db_session.refresh(match)
    return match


class TestImportInjuries:
    def test_stores_resolved_rows(self, db_session, clubs):
        home, away = clubs
        client = FakeClient({
            "injuries": [
                {
                    "player": {"id": 10, "name": "Star Striker", "reason": "Knee Injury"},
                    "team": {"name": "Arsenal"},
                    "fixture": {},
                },
                {
                    "player": {"id": 11, "name": "Winger", "reason": "Suspended"},
                    "team": {"name": "Chelsea"},
                    "fixture": {},
                },
            ]
        })

        report = import_injuries(db_session, client, league_id=39, league_name=LEAGUE, season=2025)

        assert report.stored == 2
        assert report.unresolved_teams == []
        absences = db_session.query(PlayerAbsence).all()
        assert {a.reason for a in absences} == {"Knee Injury", "Suspended"}
        assert {a.team_id for a in absences} == {home.id, away.id}

    def test_skips_an_unresolved_team_name(self, db_session, clubs):
        client = FakeClient({
            "injuries": [
                {"player": {"id": 10, "name": "Mystery Player", "reason": "Injury"}, "team": {"name": "Nonexistent FC"}}
            ]
        })

        report = import_injuries(db_session, client, league_id=39, league_name=LEAGUE, season=2025)

        assert report.stored == 0
        assert report.unresolved_teams == ["Nonexistent FC"]
        assert db_session.query(PlayerAbsence).count() == 0

    def test_a_row_missing_a_player_id_or_name_is_skipped(self, db_session, clubs):
        client = FakeClient({
            "injuries": [
                {"player": {"id": None, "name": "No Id"}, "team": {"name": "Arsenal"}},
                {"player": {"id": 99, "name": ""}, "team": {"name": "Arsenal"}},
            ]
        })

        report = import_injuries(db_session, client, league_id=39, league_name=LEAGUE, season=2025)
        assert report.stored == 0
        assert db_session.query(PlayerAbsence).count() == 0

    def test_overwrites_wholesale_on_rerun(self, db_session, clubs):
        """A recovered player simply stops appearing in the provider's list
        -- the next fetch must not leave their stale absence row behind."""

        client_a = FakeClient({
            "injuries": [{"player": {"id": 10, "name": "Star Striker", "reason": "Injury"}, "team": {"name": "Arsenal"}}]
        })
        import_injuries(db_session, client_a, league_id=39, league_name=LEAGUE, season=2025)
        assert db_session.query(PlayerAbsence).count() == 1

        client_b = FakeClient({"injuries": []})
        import_injuries(db_session, client_b, league_id=39, league_name=LEAGUE, season=2025)
        assert db_session.query(PlayerAbsence).count() == 0

    def test_ties_an_absence_to_a_specific_fixture_when_the_provider_gives_one(self, db_session, clubs, fixture_match):
        client = FakeClient({
            "injuries": [
                {
                    "player": {"id": 10, "name": "Star Striker", "reason": "Injury"},
                    "team": {"name": "Arsenal"},
                    "fixture": {"id": 555},
                }
            ]
        })

        import_injuries(db_session, client, league_id=39, league_name=LEAGUE, season=2025)

        absence = db_session.query(PlayerAbsence).one()
        assert absence.match_id == fixture_match.id

    def test_an_unmatched_fixture_id_still_stores_the_absence_without_a_match_link(self, db_session, clubs):
        client = FakeClient({
            "injuries": [
                {
                    "player": {"id": 10, "name": "Star Striker", "reason": "Injury"},
                    "team": {"name": "Arsenal"},
                    "fixture": {"id": 999999},
                }
            ]
        })

        report = import_injuries(db_session, client, league_id=39, league_name=LEAGUE, season=2025)
        assert report.stored == 1
        assert db_session.query(PlayerAbsence).one().match_id is None

    def test_reuses_an_existing_player_row_across_imports(self, db_session, clubs):
        client = FakeClient({
            "injuries": [{"player": {"id": 10, "name": "Star Striker", "reason": "Injury"}, "team": {"name": "Arsenal"}}]
        })
        import_injuries(db_session, client, league_id=39, league_name=LEAGUE, season=2025)
        import_injuries(db_session, client, league_id=39, league_name=LEAGUE, season=2025)

        assert db_session.query(Player).filter_by(api_player_id=10).count() == 1


class TestImportLineup:
    def test_stores_starters_and_substitutes_for_each_side(self, db_session, clubs, fixture_match):
        home, away = clubs
        client = FakeClient({
            "fixtures/lineups": [
                {
                    "team": {"name": "Arsenal"},
                    "startXI": [{"player": {"id": 1, "name": "Home Starter"}}],
                    "substitutes": [{"player": {"id": 2, "name": "Home Sub"}}],
                },
                {
                    "team": {"name": "Chelsea"},
                    "startXI": [{"player": {"id": 3, "name": "Away Starter"}}],
                    "substitutes": [],
                },
            ]
        })

        report = import_lineup(db_session, client, fixture_match)

        assert report.stored == 3
        rows = db_session.query(MatchLineup).all()
        starters = {r.player.name for r in rows if r.is_starter}
        subs = {r.player.name for r in rows if not r.is_starter}
        assert starters == {"Home Starter", "Away Starter"}
        assert subs == {"Home Sub"}
        home_rows = [r for r in rows if r.team_id == home.id]
        away_rows = [r for r in rows if r.team_id == away.id]
        assert len(home_rows) == 2
        assert len(away_rows) == 1

    def test_skips_a_match_that_already_has_a_stored_lineup_without_a_request(self, db_session, clubs, fixture_match):
        home, _ = clubs
        player = Player(api_player_id=1, name="Already Stored", team_id=home.id)
        db_session.add(player)
        db_session.commit()
        db_session.refresh(player)
        db_session.add(MatchLineup(match_id=fixture_match.id, team_id=home.id, player_id=player.id, is_starter=True))
        db_session.commit()

        client = FakeClient({"fixtures/lineups": [{"team": {"name": "Arsenal"}, "startXI": [], "substitutes": []}]})
        report = import_lineup(db_session, client, fixture_match)

        assert report.already_had_lineup is True
        assert client.calls == []

    def test_an_empty_response_stores_nothing_and_is_not_an_error(self, db_session, clubs, fixture_match):
        client = FakeClient({"fixtures/lineups": []})
        report = import_lineup(db_session, client, fixture_match)

        assert report.stored == 0
        assert report.already_had_lineup is False
        assert db_session.query(MatchLineup).count() == 0

    def test_returns_none_without_an_api_fixture_id(self, db_session, clubs):
        home, away = clubs
        match = Match(
            league=LEAGUE, season="2025-26", date=BASE, home_team_id=home.id, away_team_id=away.id,
            status="SCHEDULED", api_fixture_id=None,
        )
        db_session.add(match)
        db_session.commit()
        db_session.refresh(match)

        client = FakeClient({})
        assert import_lineup(db_session, client, match) is None
        assert client.calls == []

    def test_an_unmatchable_team_name_in_the_response_is_skipped(self, db_session, clubs, fixture_match):
        client = FakeClient({
            "fixtures/lineups": [
                {"team": {"name": "Totally Unrelated FC"}, "startXI": [{"player": {"id": 1, "name": "X"}}], "substitutes": []},
            ]
        })

        report = import_lineup(db_session, client, fixture_match)
        assert report.stored == 0
        assert db_session.query(MatchLineup).count() == 0


# --- the settings-to-client wiring shared with api_football_ingest's own ---
# run_live_sync_from_settings -- same monkeypatch pattern as test_live_sync.py


class TestImportInjuriesFromSettings:
    def test_is_a_noop_with_no_key(self, db_session, monkeypatch):
        monkeypatch.setattr(app_settings, "all_values", lambda db: {"api_football_key": ""})
        assert import_injuries_from_settings(db_session) == []

    def test_builds_a_client_and_imports_the_given_leagues(self, db_session, clubs, monkeypatch):
        monkeypatch.setattr(
            app_settings,
            "all_values",
            lambda db: {
                "api_football_key": "test-key",
                "api_football_host": "v3.football.api-sports.io",
                "api_football_daily_budget": 7500,
                "api_football_per_minute": 300,
            },
        )
        monkeypatch.setattr(
            squad_ingest,
            "ApiFootballClient",
            lambda key, **kwargs: FakeClient(
                {"injuries": [{"player": {"id": 10, "name": "Star Striker", "reason": "Injury"}, "team": {"name": "Arsenal"}}]},
                **kwargs,
            ),
        )

        reports = import_injuries_from_settings(db_session, league_names=[LEAGUE], season=2025)

        assert len(reports) == 1
        assert reports[0].stored == 1

    def test_stops_at_the_first_quota_error_rather_than_skipping_silently(self, db_session, monkeypatch):
        # Budget for exactly one request: the first league's injuries() call
        # succeeds and spends it, the second's then exceeds it.
        monkeypatch.setattr(
            app_settings,
            "all_values",
            lambda db: {"api_football_key": "test-key", "api_football_daily_budget": 1, "api_football_per_minute": 300},
        )
        monkeypatch.setattr(squad_ingest, "ApiFootballClient", lambda key, **kwargs: FakeClient({}, **kwargs))

        reports = import_injuries_from_settings(db_session, league_names=[LEAGUE, "Spanish La Liga"], season=2025)
        assert len(reports) == 1
        assert reports[0].league == LEAGUE


class TestRunLineupCheckFromSettings:
    def test_is_a_noop_with_no_key(self, db_session, monkeypatch):
        monkeypatch.setattr(app_settings, "all_values", lambda db: {"api_football_key": ""})
        assert run_lineup_check_from_settings(db_session) is None

    def test_checks_only_matches_within_the_window(self, db_session, clubs, monkeypatch):
        home, away = clubs
        now = dt.datetime.utcnow()
        soon = Match(
            league=LEAGUE, season="2025-26", date=now + dt.timedelta(minutes=30),
            home_team_id=home.id, away_team_id=away.id, status="SCHEDULED", api_fixture_id=1,
        )
        far_off = Match(
            league=LEAGUE, season="2025-26", date=now + dt.timedelta(days=5),
            home_team_id=home.id, away_team_id=away.id, status="SCHEDULED", api_fixture_id=2,
        )
        already_finished = Match(
            league=LEAGUE, season="2025-26", date=now - dt.timedelta(minutes=30),
            home_team_id=home.id, away_team_id=away.id, status="FINISHED", api_fixture_id=3,
        )
        db_session.add_all([soon, far_off, already_finished])
        db_session.commit()

        monkeypatch.setattr(
            app_settings,
            "all_values",
            lambda db: {
                "api_football_key": "test-key",
                "api_football_host": "v3.football.api-sports.io",
                "api_football_daily_budget": 7500,
                "api_football_per_minute": 300,
            },
        )
        monkeypatch.setattr(
            squad_ingest, "ApiFootballClient", lambda key, **kwargs: FakeClient({"fixtures/lineups": []}, **kwargs)
        )

        run = run_lineup_check_from_settings(db_session, window_minutes=90)

        assert run.considered == 1  # only `soon` falls in the next 90 minutes

    def test_reports_matches_whose_lineup_was_just_confirmed(self, db_session, clubs, monkeypatch, fixture_match):
        monkeypatch.setattr(
            app_settings,
            "all_values",
            lambda db: {
                "api_football_key": "test-key",
                "api_football_host": "v3.football.api-sports.io",
                "api_football_daily_budget": 7500,
                "api_football_per_minute": 300,
            },
        )
        fixture_match.date = dt.datetime.utcnow() + dt.timedelta(minutes=30)
        db_session.commit()
        monkeypatch.setattr(
            squad_ingest,
            "ApiFootballClient",
            lambda key, **kwargs: FakeClient(
                {"fixtures/lineups": [{"team": {"name": "Arsenal"}, "startXI": [{"player": {"id": 1, "name": "X"}}], "substitutes": []}]},
                **kwargs,
            ),
        )

        run = run_lineup_check_from_settings(db_session, window_minutes=90)

        assert run.newly_confirmed == [fixture_match.id]
