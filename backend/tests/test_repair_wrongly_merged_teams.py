"""scripts/repair_wrongly_merged_teams.py undoes the real September 19
production incident this project's team-matching fix (team_matching.py's
_CITY_ONLY_TOKENS / _FALSE_COGNATE_PAIRS) was written to prevent: AC Milan's
entire history fused into FC Internazionale Milano's row by a bad run of
merge_duplicate_teams.py, with "AC Milan" left behind as one of
Internazionale's aliases.

These tests stand in a fake openfootball feed (never a real network call)
reproducing a small slice of that exact corruption, then check that the
repair script (a) does not silently no-op because of the shadowing alias,
(b) writes nothing at all in dry-run mode despite calling
import_openfootball_season, which commits internally, and (c) with
--delete, creates the correct fresh rows, deletes exactly the
wrongly-attributed ones, and leaves everything else -- including a
match no fresh import can confirm -- alone.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from pathlib import Path

import pytest

from app.data.providers.openfootball import OpenFootballMatch
from app.db.models import Match, Team

LEAGUE = "Italian Serie A"
SEASON = "2024-25"

D1 = dt.datetime(2024, 10, 6, 15, 0)   # the derby, corrupted into a self-play row
D2 = dt.datetime(2024, 10, 13, 15, 0)  # Internazionale's own real, correctly-attributed fixture
D3 = dt.datetime(2024, 10, 20, 15, 0)  # AC Milan (home) -- wrongly attributed to the survivor
D4 = dt.datetime(2024, 10, 27, 15, 0)  # AC Milan (away) -- wrongly attributed to the survivor
D5 = dt.datetime(2024, 11, 3, 15, 0)   # a wrong-looking row with no fresh fixture to confirm it


@pytest.fixture()
def script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "repair_wrongly_merged_teams.py"
    spec = importlib.util.spec_from_file_location("repair_wrongly_merged_teams_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LA_LIGA = "Spanish La Liga"
E1 = dt.datetime(2024, 9, 1, 20, 0)  # Atletico (wrongly attributed to Real Madrid CF)
E2 = dt.datetime(2024, 9, 8, 20, 0)  # Rayo (wrongly attributed to the same survivor)


@pytest.fixture(autouse=True)
def fake_openfootball(monkeypatch):
    def fetch_season(league_name: str, season: str, timeout: int = 30):
        if season != SEASON:
            return []
        if league_name == LEAGUE:
            rows = [
                ("AC Milan", "FC Internazionale Milano", D1, 2, 1),
                ("FC Internazionale Milano", "US Lecce", D2, 1, 1),
                ("AC Milan", "US Lecce", D3, 3, 0),
                ("US Lecce", "AC Milan", D4, 0, 2),
            ]
        elif league_name == LA_LIGA:
            rows = [
                ("Club Atlético de Madrid", "Sevilla FC", E1, 1, 1),
                ("Rayo Vallecano de Madrid", "Sevilla FC", E2, 2, 2),
            ]
        else:
            return []
        return [
            OpenFootballMatch(
                league=league_name, season=season, date=date,
                home_team=home, away_team=away,
                home_score=hs, away_score=as_, ht_home_score=None, ht_away_score=None,
            )
            for home, away, date, hs, as_ in rows
        ]

    monkeypatch.setattr("app.data.providers.openfootball.fetch_season", fetch_season)


@pytest.fixture()
def corrupted_state(db_session):
    """The production incident, shrunk to two clubs and five matches:
    Internazionale as the survivor, carrying AC Milan's entire history plus
    AC Milan's own exact name as an alias -- exactly what
    merge_duplicate_teams.py's real run left behind."""

    lecce = Team(name="US Lecce", league=LEAGUE, aliases=[])
    survivor = Team(name="FC Internazionale Milano", league=LEAGUE, aliases=["AC Milan"])
    db_session.add_all([lecce, survivor])
    db_session.commit()
    db_session.refresh(lecce)
    db_session.refresh(survivor)

    matches = {
        "derby_artifact": Match(
            league=LEAGUE, season=SEASON, date=D1, status="FINISHED",
            home_team_id=survivor.id, away_team_id=survivor.id, home_score=2, away_score=1,
        ),
        "genuine": Match(
            league=LEAGUE, season=SEASON, date=D2, status="FINISHED",
            home_team_id=survivor.id, away_team_id=lecce.id, home_score=1, away_score=1,
        ),
        "wrong_home": Match(
            league=LEAGUE, season=SEASON, date=D3, status="FINISHED",
            home_team_id=survivor.id, away_team_id=lecce.id, home_score=3, away_score=0,
        ),
        "wrong_away": Match(
            league=LEAGUE, season=SEASON, date=D4, status="FINISHED",
            home_team_id=lecce.id, away_team_id=survivor.id, home_score=0, away_score=2,
        ),
        "unconfirmed": Match(
            league=LEAGUE, season=SEASON, date=D5, status="FINISHED",
            home_team_id=survivor.id, away_team_id=lecce.id, home_score=5, away_score=5,
        ),
    }
    db_session.add_all(matches.values())
    db_session.commit()
    for m in matches.values():
        db_session.refresh(m)

    return {"lecce": lecce, "survivor": survivor, **matches}


def test_dry_run_writes_nothing_despite_the_internally_committing_reimport(db_session, script, corrupted_state, capsys):
    """import_openfootball_season commits internally -- the one thing this
    test exists to prove is that a dry run of this script still leaves the
    database exactly as it found it."""

    survivor_id = corrupted_state["survivor"].id

    sys.argv = ["repair_wrongly_merged_teams.py"]
    script.main()

    db_session.expire_all()
    assert db_session.query(Team).filter(Team.league == LEAGUE).count() == 2
    assert db_session.query(Match).filter(Match.league == LEAGUE).count() == 5
    survivor = db_session.get(Team, survivor_id)
    assert survivor.aliases == ["AC Milan"]

    out = capsys.readouterr().out
    assert "Dry run -- nothing written" in out
    assert "3 match(es) belong to 'AC Milan'" in out


def test_shadowing_alias_would_otherwise_hide_the_bug(db_session, script, corrupted_state):
    """Without stripping "AC Milan" from the survivor's aliases first,
    TeamResolver resolves the re-imported "AC Milan" fixtures straight back
    to the survivor and no new team is ever created. Delete the alias
    manually to reproduce the bug this test guards against, confirming the
    real fix (removing it automatically) is what makes the repair work."""

    survivor = corrupted_state["survivor"]
    # Simulate the *old*, buggy script by leaving the alias in place and
    # calling the re-import step directly.
    from app.data.ingest import import_openfootball_season
    import_openfootball_season(db_session, LEAGUE, SEASON)
    db_session.expire_all()

    assert db_session.query(Team).filter(Team.league == LEAGUE, Team.name == "AC Milan").first() is None
    survivor_after = db_session.get(Team, survivor.id)
    assert "AC Milan" in (survivor_after.aliases or [])


def test_delete_creates_correct_rows_and_removes_only_the_wrong_ones(db_session, script, corrupted_state):
    # Captured before script.main() runs -- expire_all() below leaves the
    # ORM objects backing corrupted_state expired, and reading .id off one
    # whose row main() deleted would itself raise ObjectDeletedError.
    ids = {key: obj.id for key, obj in corrupted_state.items()}

    sys.argv = ["repair_wrongly_merged_teams.py", "--delete"]
    script.main()

    db_session.expire_all()

    ac_milan = db_session.query(Team).filter(Team.league == LEAGUE, Team.name == "AC Milan").one()
    survivor = db_session.get(Team, ids["survivor"])
    lecce = db_session.get(Team, ids["lecce"])
    assert "AC Milan" not in (survivor.aliases or [])

    # The three wrongly-attributed rows are gone.
    for key in ("derby_artifact", "wrong_home", "wrong_away"):
        assert db_session.get(Match, ids[key]) is None

    # The genuine, already-correct fixture and the unconfirmed one were
    # never touched.
    assert db_session.get(Match, ids["genuine"]) is not None
    assert db_session.get(Match, ids["unconfirmed"]) is not None

    def find(home_id, away_id, date):
        return db_session.query(Match).filter(
            Match.league == LEAGUE, Match.home_team_id == home_id,
            Match.away_team_id == away_id, Match.date == date,
        ).one()

    derby = find(ac_milan.id, survivor.id, D1)
    assert (derby.home_score, derby.away_score) == (2, 1)

    home_leg = find(ac_milan.id, lecce.id, D3)
    assert (home_leg.home_score, home_leg.away_score) == (3, 0)

    away_leg = find(lecce.id, ac_milan.id, D4)
    assert (away_leg.home_score, away_leg.away_score) == (0, 2)

    assert db_session.query(Match).filter(Match.league == LEAGUE).count() == 5


def test_missing_survivor_is_skipped_not_errored(db_session, script, capsys):
    """None of the five bad-merge survivors exist in this test's database at
    all (only the Serie A pair is set up elsewhere) -- every entry for a
    league with no data must be skipped cleanly rather than raising."""

    sys.argv = ["repair_wrongly_merged_teams.py"]
    script.main()

    out = capsys.readouterr().out
    assert "not found -- skipping" in out


def test_a_survivor_with_two_victims_is_not_double_counted(db_session, script, capsys):
    """Real Madrid CF appears twice in BAD_MERGES -- once for Atletico, once
    for Rayo -- because it absorbed both. _survivor_fingerprint_plan depends
    only on the survivor, so both entries compute the identical wrong-match
    list; summing len(wrong) once per BAD_MERGES row (as the very first
    version of this script did) double-counts every one of Real Madrid's
    wrongly-attributed matches into the reported total. The real production
    dry run surfaced exactly this: 425 matches reported once per victim,
    inflating the total by 425 extra."""

    sevilla = Team(name="Sevilla FC", league=LA_LIGA, aliases=[])
    real_madrid = Team(
        name="Real Madrid CF", league=LA_LIGA,
        aliases=["Club Atlético de Madrid", "Rayo Vallecano de Madrid"],
    )
    db_session.add_all([sevilla, real_madrid])
    db_session.commit()
    db_session.refresh(sevilla)
    db_session.refresh(real_madrid)

    db_session.add_all([
        Match(
            league=LA_LIGA, season=SEASON, date=E1, status="FINISHED",
            home_team_id=real_madrid.id, away_team_id=sevilla.id, home_score=1, away_score=1,
        ),
        Match(
            league=LA_LIGA, season=SEASON, date=E2, status="FINISHED",
            home_team_id=real_madrid.id, away_team_id=sevilla.id, home_score=2, away_score=2,
        ),
    ])
    db_session.commit()

    sys.argv = ["repair_wrongly_merged_teams.py"]
    script.main()

    out = capsys.readouterr().out
    assert out.count("'Real Madrid CF'") == 1
    assert "2 match(es) belong to 'Club Atlético de Madrid' / 'Rayo Vallecano de Madrid'" in out
    assert "Dry run -- nothing written. 2 match(es) would be deleted" in out
