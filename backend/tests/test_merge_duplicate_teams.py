"""Merging the club-identity fragments TeamIndex.resolve() used to create
before it learned to prefer a same-league candidate on a tie: an
established club row and a freshly minted stub for the same club, under a
different spelling.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from pathlib import Path

import pytest

from app.db.models import EloHistory, Match, Team

BASE = dt.datetime(2026, 10, 4, 15, 0)


@pytest.fixture()
def script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "merge_duplicate_teams.py"
    spec = importlib.util.spec_from_file_location("merge_duplicate_teams_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _opponent(db_session, league="English Premier League") -> Team:
    opponent = Team(name="Arsenal FC", league=league, aliases=[])
    db_session.add(opponent)
    db_session.commit()
    db_session.refresh(opponent)
    return opponent


def test_finds_a_same_league_duplicate_by_fuzzy_name(db_session, script):
    established = Team(name="AFC Bournemouth", league="English Premier League", aliases=[])
    stub = Team(name="Bournemouth", league="English Premier League", aliases=[])
    db_session.add_all([established, stub])
    db_session.commit()

    groups = script.find_groups(db_session, "English Premier League")

    assert len(groups) == 1
    assert {t.id for t in groups[0]} == {established.id, stub.id}


def test_does_not_group_different_clubs_that_merely_share_a_city(db_session, script):
    """A dry run against this project's real data once proposed merging AC
    Milan with Internazionale, Real Madrid with Atletico Madrid and with
    Rayo Vallecano, and FC Barcelona with RCD Espanyol -- four different
    clubs each collapsed to a shared city token ("milan"/"milano",
    "madrid", "barcelona") by find_groups' bare primary-score check, which
    never looked at how much of the *other* name was left unaccounted for.
    None of these may ever cluster together, in either league."""

    serie_a = [
        Team(name="AC Milan", league="Italian Serie A", aliases=[]),
        Team(name="FC Internazionale Milano", league="Italian Serie A", aliases=[]),
    ]
    la_liga = [
        Team(name="Real Madrid CF", league="Spanish La Liga", aliases=[]),
        Team(name="Club Atlético de Madrid", league="Spanish La Liga", aliases=[]),
        Team(name="Rayo Vallecano de Madrid", league="Spanish La Liga", aliases=[]),
        Team(name="FC Barcelona", league="Spanish La Liga", aliases=[]),
        Team(name="RCD Espanyol de Barcelona", league="Spanish La Liga", aliases=[]),
    ]
    db_session.add_all(serie_a + la_liga)
    db_session.commit()

    assert script.find_groups(db_session, "Italian Serie A") == []
    assert script.find_groups(db_session, "Spanish La Liga") == []


def test_still_finds_the_real_duplicate_among_same_city_clubs(db_session, script):
    """The fix must not overcorrect into refusing every match involving one
    of these clubs -- Inter's own two spellings are still the same club and
    must still be found, right alongside the unrelated AC Milan row that
    must not be swept in."""

    ac_milan = Team(name="AC Milan", league="Italian Serie A", aliases=[])
    inter_full = Team(name="FC Internazionale Milano", league="Italian Serie A", aliases=[])
    inter_short = Team(name="Inter", league="Italian Serie A", aliases=[])
    db_session.add_all([ac_milan, inter_full, inter_short])
    db_session.commit()

    groups = script.find_groups(db_session, "Italian Serie A")
    assert len(groups) == 1
    assert {t.id for t in groups[0]} == {inter_full.id, inter_short.id}


def test_does_not_group_a_promoted_clubs_two_divisions(db_session, script):
    """The exact case this must never touch: a club's Championship history
    and its Premier League row are two real, separate histories, not a
    duplicate -- even though the names are identical."""

    championship = Team(name="AFC Bournemouth", league="English Championship", aliases=[])
    epl = Team(name="AFC Bournemouth", league="English Premier League", aliases=[])
    db_session.add_all([championship, epl])
    db_session.commit()

    groups = script.find_groups(db_session)
    assert groups == []


def test_a_league_with_no_duplicates_reports_none_found(db_session, script, capsys):
    db_session.add(Team(name="Arsenal FC", league="English Premier League", aliases=[]))
    db_session.commit()

    sys.argv = ["merge_duplicate_teams.py", "--league", "English Premier League"]
    script.main()

    assert "No duplicate club groups found" in capsys.readouterr().out


def test_dry_run_changes_nothing(db_session, script, capsys):
    established = Team(name="AFC Bournemouth", league="English Premier League", aliases=[])
    stub = Team(name="Bournemouth", league="English Premier League", aliases=[])
    db_session.add_all([established, stub])
    db_session.commit()
    db_session.refresh(established)
    db_session.refresh(stub)

    sys.argv = ["merge_duplicate_teams.py", "--league", "English Premier League"]
    script.main()

    assert db_session.query(Team).count() == 2
    out = capsys.readouterr().out
    assert "Dry run" in out
    assert f"#{established.id}" in out and f"#{stub.id}" in out


def test_merge_keeps_the_row_with_more_matches(db_session, script):
    opponent = _opponent(db_session)
    established = Team(name="AFC Bournemouth", league="English Premier League", aliases=[])
    stub = Team(name="Bournemouth", league="English Premier League", aliases=[])
    db_session.add_all([established, stub])
    db_session.commit()
    db_session.refresh(established)
    db_session.refresh(stub)
    stub_id = stub.id

    db_session.add(Match(
        league="English Premier League", season="2026", date=BASE,
        home_team_id=established.id, away_team_id=opponent.id, status="FINISHED",
        home_score=2, away_score=1,
    ))
    db_session.commit()

    sys.argv = ["merge_duplicate_teams.py", "--league", "English Premier League", "--delete"]
    script.main()

    db_session.expire_all()
    assert db_session.query(Team).count() == 2  # opponent + survivor
    assert db_session.get(Team, established.id) is not None
    assert db_session.get(Team, stub_id) is None


def test_matches_move_on_both_home_and_away_side(db_session, script):
    opponent = _opponent(db_session)
    established = Team(name="AFC Bournemouth", league="English Premier League", aliases=[])
    stub = Team(name="Bournemouth", league="English Premier League", aliases=[])
    db_session.add_all([established, stub])
    db_session.commit()
    db_session.refresh(established)
    db_session.refresh(stub)

    db_session.add(Match(
        league="English Premier League", season="2026", date=BASE,
        home_team_id=established.id, away_team_id=opponent.id, status="FINISHED", home_score=2, away_score=1,
    ))
    db_session.add(Match(
        league="English Premier League", season="2026", date=BASE + dt.timedelta(days=7),
        home_team_id=opponent.id, away_team_id=stub.id, status="SCHEDULED",
    ))
    db_session.commit()

    sys.argv = ["merge_duplicate_teams.py", "--league", "English Premier League", "--delete"]
    script.main()

    db_session.expire_all()
    matches = db_session.query(Match).order_by(Match.date).all()
    assert matches[0].home_team_id == established.id
    assert matches[1].away_team_id == established.id


def test_elo_history_moves_too(db_session, script):
    """The gap in the original version of this script: it moved matches but
    never touched elo_history.team_id, a separate foreign key to teams.id
    -- deleting the merged-away row would have orphaned it."""

    opponent = _opponent(db_session)
    established = Team(name="AFC Bournemouth", league="English Premier League", aliases=[])
    stub = Team(name="Bournemouth", league="English Premier League", aliases=[])
    db_session.add_all([established, stub])
    db_session.commit()
    db_session.refresh(established)
    db_session.refresh(stub)

    # The match belongs to the established row, so it -- not the stub --
    # has more history and survives; the Elo row is what must move.
    match = Match(
        league="English Premier League", season="2026", date=BASE,
        home_team_id=established.id, away_team_id=opponent.id, status="FINISHED", home_score=1, away_score=1,
    )
    db_session.add(match)
    db_session.commit()
    db_session.refresh(match)

    db_session.add(EloHistory(team_id=stub.id, match_id=match.id, date=BASE, rating_before=1500, rating_after=1505))
    db_session.commit()

    sys.argv = ["merge_duplicate_teams.py", "--league", "English Premier League", "--delete"]
    script.main()

    db_session.expire_all()
    history = db_session.query(EloHistory).one()
    assert history.team_id == established.id


def test_aliases_merge_and_include_the_removed_names(db_session, script):
    established = Team(name="AFC Bournemouth", league="English Premier League", aliases=["The Cherries"])
    stub = Team(name="Bournemouth", league="English Premier League", aliases=[])
    db_session.add_all([established, stub])
    db_session.commit()

    sys.argv = ["merge_duplicate_teams.py", "--league", "English Premier League", "--delete"]
    script.main()

    db_session.expire_all()
    survivor = db_session.query(Team).one()
    assert set(survivor.aliases) == {"The Cherries", "Bournemouth"}
