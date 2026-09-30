"""scripts/generate_random_picks.py publishes COUNT randomly-drawn slips per
cadence (daily/weekly) onto the same admin_picks table a human admin curates
by hand and scripts/generate_weekly_picks.py's three risk tiers already use --
distinguished only by AdminPick.source, so re-running one cadence replaces
its own rows instead of piling up duplicates, and never touches a row an
admin (or the other cadence) created.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from pathlib import Path

import pytest

from app.db.models import AdminPick, Match, Prediction, Team

BASE = dt.datetime.utcnow() + dt.timedelta(hours=6)


@pytest.fixture()
def script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "generate_random_picks.py"
    spec = importlib.util.spec_from_file_location("generate_random_picks_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _prediction(match_id: int) -> Prediction:
    return Prediction(
        match_id=match_id, model_version="test", home_win=0.75, draw=0.15, away_win=0.10,
        over_probabilities={"2.5": 0.60}, btts_yes=0.55, btts_no=0.45,
        correct_score_probabilities={"2-1": 0.11}, most_likely_score="2-1", most_likely_score_probability=0.11,
        global_outcome_market="Match Result", global_outcome_selection="Home Win", global_outcome_probability=0.75,
        confidence="HIGH", data_quality_score=1.0, model_agreement_score=0.9,
        explanation={"positive": [], "negative": []}, model_breakdown={},
    )


def _favorite_matches(db_session, count: int = 20) -> list[Match]:
    """count matches, each a 75%-favored (well above the script's 60% bar)
    home win -- no MatchOdds needed, since the random-pick engine books
    unpriced (see select_random_legs / resolve_legs_unpriced)."""

    matches = []
    for i in range(count):
        home = Team(name=f"Home{i}", league="English Premier League", aliases=[])
        away = Team(name=f"Away{i}", league="English Premier League", aliases=[])
        db_session.add_all([home, away])
        db_session.commit()
        db_session.refresh(home)
        db_session.refresh(away)
        match = Match(
            league="English Premier League", season="2025-26", date=BASE + dt.timedelta(days=1, hours=i),
            home_team_id=home.id, away_team_id=away.id, status="SCHEDULED",
        )
        db_session.add(match)
        db_session.commit()
        db_session.refresh(match)
        db_session.add(_prediction(match.id))
        matches.append(match)
    db_session.commit()
    return matches


def test_generates_five_slips_for_each_requested_cadence(db_session, script):
    _favorite_matches(db_session)

    sys.argv = ["generate_random_picks.py", "--which", "both"]
    script.main()

    db_session.expire_all()
    picks = {p.source: p for p in db_session.query(AdminPick).all()}
    expected_sources = {f"system_random_{cadence}_{n}" for cadence in ("daily", "weekly") for n in range(1, 6)}
    assert set(picks) == expected_sources
    for pick in picks.values():
        assert 10 <= len(pick.legs) <= 15
        assert pick.priced is False
        assert pick.booking_code is None


def test_which_daily_only_touches_daily_rows(db_session, script):
    _favorite_matches(db_session)

    sys.argv = ["generate_random_picks.py", "--which", "daily"]
    script.main()

    db_session.expire_all()
    sources = {p.source for p in db_session.query(AdminPick).all()}
    assert all(s.startswith("system_random_daily_") for s in sources)
    assert len(sources) == 5


def test_rerun_replaces_the_same_rows_rather_than_duplicating(db_session, script):
    _favorite_matches(db_session)
    sys.argv = ["generate_random_picks.py", "--which", "daily"]

    script.main()
    db_session.expire_all()
    first_ids = {p.source: p.id for p in db_session.query(AdminPick).all()}

    script.main()
    db_session.expire_all()
    rows = db_session.query(AdminPick).all()
    assert len(rows) == 5
    for row in rows:
        assert row.id == first_ids[row.source]


def test_regeneration_clears_a_stale_booking_code(db_session, script):
    _favorite_matches(db_session)
    sys.argv = ["generate_random_picks.py", "--which", "daily"]

    script.main()
    db_session.expire_all()
    pick = db_session.query(AdminPick).filter(AdminPick.source == "system_random_daily_1").one()
    pick.booking_code = "STALE123"
    pick.booking_code_bookmaker = "SportyBet"
    db_session.commit()

    script.main()
    db_session.expire_all()
    pick = db_session.query(AdminPick).filter(AdminPick.source == "system_random_daily_1").one()
    assert pick.booking_code is None
    assert pick.booking_code_bookmaker is None


def test_admin_authored_and_other_cadence_picks_are_never_touched(db_session, script):
    matches = _favorite_matches(db_session)
    admin_pick = AdminPick(
        legs=[{"match_id": matches[0].id, "market": "Match Result", "selection": "Home Win"}],
        priced=True, label="Hand-picked banker", source=None,
        expires_at=matches[0].date + dt.timedelta(days=2),
    )
    weekly_pick = AdminPick(
        source="system_random_weekly_1", legs=[], priced=False, label="Last week's draw #1",
        expires_at=dt.datetime.utcnow() + dt.timedelta(days=5),
    )
    db_session.add_all([admin_pick, weekly_pick])
    db_session.commit()
    db_session.refresh(admin_pick)
    db_session.refresh(weekly_pick)

    sys.argv = ["generate_random_picks.py", "--which", "daily"]
    script.main()

    db_session.expire_all()
    untouched_admin = db_session.get(AdminPick, admin_pick.id)
    assert untouched_admin is not None
    assert untouched_admin.label == "Hand-picked banker"

    untouched_weekly = db_session.get(AdminPick, weekly_pick.id)
    assert untouched_weekly is not None
    assert untouched_weekly.label == "Last week's draw #1"


def test_too_few_qualifying_matches_leaves_previous_rows_alone(db_session, script):
    _favorite_matches(db_session, count=2)  # below the "at least 3 legs" floor the script writes

    stale = AdminPick(
        source="system_random_daily_1", legs=[], priced=False, label="Yesterday's draw #1",
        expires_at=dt.datetime.utcnow() + dt.timedelta(days=2),
    )
    db_session.add(stale)
    db_session.commit()
    db_session.refresh(stale)

    sys.argv = ["generate_random_picks.py", "--which", "daily"]
    script.main()

    db_session.expire_all()
    still_there = db_session.get(AdminPick, stale.id)
    assert still_there is not None
    assert still_there.label == "Yesterday's draw #1"


def test_dry_run_writes_nothing(db_session, script, capsys):
    _favorite_matches(db_session)

    sys.argv = ["generate_random_picks.py", "--which", "both", "--dry-run"]
    script.main()

    assert db_session.query(AdminPick).count() == 0
    assert "Dry run -- nothing written" in capsys.readouterr().out
