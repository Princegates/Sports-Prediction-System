"""The repair for the corruption import_fixtures could write before it
learned to reconcile a moved kickoff: the same fixture, twice.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from pathlib import Path

import pytest

from app.auth.passwords import hash_password
from app.db.models import ChatMessage, Match, MatchOdds, Prediction, Team, User

BASE = dt.datetime(2026, 10, 4, 15, 0)


@pytest.fixture()
def script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "repair_rescheduled_duplicates.py"
    spec = importlib.util.spec_from_file_location("repair_rescheduled_duplicates_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def clubs(db_session):
    home = Team(name="Real Madrid CF", league="Spanish La Liga", aliases=[])
    away = Team(name="Arsenal FC", league="English Premier League", aliases=[])
    db_session.add_all([home, away])
    db_session.commit()
    for t in (home, away):
        db_session.refresh(t)
    return home, away


def _pair(db_session, clubs, *, gap_days: float, league="Spanish La Liga"):
    home, away = clubs
    older = Match(league=league, season="2026", date=BASE, home_team_id=home.id, away_team_id=away.id, status="SCHEDULED")
    db_session.add(older)
    db_session.commit()
    db_session.refresh(older)
    newer = Match(
        league=league, season="2026", date=BASE + dt.timedelta(days=gap_days),
        home_team_id=home.id, away_team_id=away.id, status="SCHEDULED",
    )
    db_session.add(newer)
    db_session.commit()
    db_session.refresh(newer)
    return older, newer


def test_finds_a_pair_within_the_reschedule_window(db_session, clubs, script):
    older, newer = _pair(db_session, clubs, gap_days=3)
    pairs = script.find_pairs(db_session, "Spanish La Liga")
    assert pairs == [(older, newer)]


def test_does_not_pair_a_genuine_second_meeting_months_later(db_session, clubs, script):
    _pair(db_session, clubs, gap_days=120)
    assert script.find_pairs(db_session, "Spanish La Liga") == []


def test_ignores_a_finished_match_entirely(db_session, clubs, script):
    home, away = clubs
    db_session.add(Match(
        league="Spanish La Liga", season="2026", date=BASE, home_team_id=home.id, away_team_id=away.id,
        status="FINISHED", home_score=1, away_score=0,
    ))
    db_session.add(Match(
        league="Spanish La Liga", season="2026", date=BASE + dt.timedelta(days=2),
        home_team_id=home.id, away_team_id=away.id, status="SCHEDULED",
    ))
    db_session.commit()
    assert script.find_pairs(db_session, "Spanish La Liga") == []


def test_dry_run_changes_nothing(db_session, clubs, script, capsys):
    older, newer = _pair(db_session, clubs, gap_days=3)

    sys.argv = ["repair_rescheduled_duplicates.py", "--league", "Spanish La Liga"]
    script.main()

    assert db_session.query(Match).count() == 2
    out = capsys.readouterr().out
    assert "Dry run" in out
    assert f"#{older.id}" in out and f"#{newer.id}" in out


def test_merge_keeps_the_older_row_with_the_newer_date(db_session, clubs, script):
    older, newer = _pair(db_session, clubs, gap_days=3)
    newer_id, newer_date = newer.id, newer.date

    sys.argv = ["repair_rescheduled_duplicates.py", "--league", "Spanish La Liga", "--delete"]
    script.main()

    # The script runs its own session; this test's session cached the row
    # before that ran and would otherwise hand back its stale identity-map
    # copy rather than reflecting the delete.
    db_session.expire_all()

    assert db_session.query(Match).count() == 1
    survivor = db_session.query(Match).one()
    assert survivor.id == older.id
    assert survivor.date == newer_date
    assert db_session.get(Match, newer_id) is None


def test_dependents_move_to_the_surviving_row(db_session, clubs, script):
    older, newer = _pair(db_session, clubs, gap_days=3)
    db_session.add(MatchOdds(match_id=newer.id, bookmaker="Bet365", market="Match Result", selection="Home Win", decimal_odds=1.8))
    db_session.add(Prediction(
        match_id=newer.id, model_version="test", home_win=0.5, draw=0.3, away_win=0.2,
        over_probabilities={}, btts_yes=0.5, btts_no=0.5, correct_score_probabilities={},
        most_likely_score="1-0", most_likely_score_probability=0.1,
        global_outcome_market="Match Result", global_outcome_selection="Home Win", global_outcome_probability=0.5,
        confidence="LOW", data_quality_score=0.5, model_agreement_score=0.5,
        explanation={"positive": [], "negative": []}, model_breakdown={},
    ))
    db_session.commit()

    sys.argv = ["repair_rescheduled_duplicates.py", "--league", "Spanish La Liga", "--delete"]
    script.main()

    db_session.expire_all()
    odds = db_session.query(MatchOdds).one()
    assert odds.match_id == older.id
    prediction = db_session.query(Prediction).one()
    assert prediction.match_id == older.id


def test_a_chat_reference_moves_rather_than_breaks(db_session, clubs, script):
    older, newer = _pair(db_session, clubs, gap_days=3)
    user = User(email="chat@example.com", name="Chat User", password_hash=hash_password("x"), role="user", status="active")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    db_session.add(ChatMessage(
        user_id=user.id, role="user", content="tell me about this match", context_match_id=newer.id,
    ))
    db_session.commit()

    sys.argv = ["repair_rescheduled_duplicates.py", "--league", "Spanish La Liga", "--delete"]
    script.main()

    db_session.expire_all()
    message = db_session.query(ChatMessage).one()
    assert message.context_match_id == older.id


def test_three_rows_sharing_a_pair_merge_pairwise(db_session, clubs, script):
    """Not the common case, but must not crash or silently drop the third."""

    home, away = clubs
    ids = []
    for offset in (0, 2, 5):
        m = Match(
            league="Spanish La Liga", season="2026", date=BASE + dt.timedelta(days=offset),
            home_team_id=home.id, away_team_id=away.id, status="SCHEDULED",
        )
        db_session.add(m)
        db_session.commit()
        db_session.refresh(m)
        ids.append(m.id)

    sys.argv = ["repair_rescheduled_duplicates.py", "--league", "Spanish La Liga", "--delete"]
    script.main()

    db_session.expire_all()
    assert db_session.query(Match).count() == 2  # first pair merged, third stands alone


def test_a_league_with_no_duplicates_reports_none_found(db_session, clubs, script, capsys):
    home, away = clubs
    db_session.add(Match(
        league="Spanish La Liga", season="2026", date=BASE, home_team_id=home.id, away_team_id=away.id,
        status="SCHEDULED",
    ))
    db_session.commit()

    sys.argv = ["repair_rescheduled_duplicates.py", "--league", "Spanish La Liga"]
    script.main()

    assert "No duplicate pairs found" in capsys.readouterr().out
