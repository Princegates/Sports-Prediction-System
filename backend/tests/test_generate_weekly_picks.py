"""scripts/generate_weekly_picks.py publishes the week's three system
accumulators (Low/Medium/High risk, always 10 legs, pooled across every
league) onto the same admin_picks table a human admin curates by hand --
distinguished only by AdminPick.source, so re-running it replaces each
tier's own row instead of piling up duplicates, and never touches a row an
admin created themselves.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import math
import sys
from pathlib import Path

import pytest

from app.db.models import AdminPick, Match, MatchOdds, Prediction, Team

BASE = dt.datetime.utcnow() + dt.timedelta(hours=6)

# 40 matches on a wide, evenly-stepped odds ladder -- verified (see the
# module this mirrors, test_betcode_selection.py) to put a qualifying
# 10-leg window inside all three of low (5-10), medium (11-20) and high
# (21-30) combined odds at once, so a single fixture exercises every tier.
LADDER = [round(1.05 + 0.02 * i, 3) for i in range(40)]


@pytest.fixture()
def script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "generate_weekly_picks.py"
    spec = importlib.util.spec_from_file_location("generate_weekly_picks_under_test", path)
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


def _ladder_matches(db_session, odds_list: list[float] = LADDER) -> list[Match]:
    matches = []
    for i, odds in enumerate(odds_list):
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
        db_session.add(MatchOdds(
            match_id=match.id, bookmaker="Bet9ja", market="Match Result", selection="Home Win", decimal_odds=odds,
        ))
        matches.append(match)
    db_session.commit()
    return matches


def _stored_combined_odds(pick: AdminPick, db_session) -> float:
    odds = 1.0
    for leg in pick.legs:
        row = db_session.query(MatchOdds).filter(
            MatchOdds.match_id == leg["match_id"], MatchOdds.market == leg["market"], MatchOdds.selection == leg["selection"],
        ).one()
        odds *= row.decimal_odds
    return odds


def test_generates_all_three_tiers_within_their_bands(db_session, script):
    _ladder_matches(db_session)

    sys.argv = ["generate_weekly_picks.py"]
    script.main()

    db_session.expire_all()
    picks = {p.source: p for p in db_session.query(AdminPick).all()}
    assert set(picks) == {"system_weekly_low", "system_weekly_medium", "system_weekly_high"}

    bands = {"system_weekly_low": (5.0, 10.0), "system_weekly_medium": (11.0, 20.0), "system_weekly_high": (21.0, 30.0)}
    for source, (lo, hi) in bands.items():
        pick = picks[source]
        assert len(pick.legs) == 10
        assert pick.priced is True
        assert pick.booking_code is None
        combined = _stored_combined_odds(pick, db_session)
        assert lo <= combined <= hi, f"{source}: {combined} not in [{lo}, {hi}]"


def test_rerun_replaces_the_same_row_rather_than_duplicating(db_session, script):
    _ladder_matches(db_session)
    sys.argv = ["generate_weekly_picks.py"]

    script.main()
    db_session.expire_all()
    first_id = db_session.query(AdminPick).filter(AdminPick.source == "system_weekly_low").one().id

    script.main()
    db_session.expire_all()
    rows = db_session.query(AdminPick).filter(AdminPick.source == "system_weekly_low").all()
    assert len(rows) == 1
    assert rows[0].id == first_id


def test_regeneration_clears_a_stale_booking_code(db_session, script):
    _ladder_matches(db_session)
    sys.argv = ["generate_weekly_picks.py"]

    script.main()
    db_session.expire_all()
    pick = db_session.query(AdminPick).filter(AdminPick.source == "system_weekly_low").one()
    pick.booking_code = "STALE123"
    pick.booking_code_bookmaker = "SportyBet"
    db_session.commit()

    script.main()
    db_session.expire_all()
    pick = db_session.query(AdminPick).filter(AdminPick.source == "system_weekly_low").one()
    assert pick.booking_code is None
    assert pick.booking_code_bookmaker is None


def test_admin_authored_picks_are_never_touched(db_session, script):
    matches = _ladder_matches(db_session)
    admin_pick = AdminPick(
        legs=[{"match_id": matches[0].id, "market": "Match Result", "selection": "Home Win"}],
        priced=True, label="Hand-picked banker", source=None,
        expires_at=matches[0].date + dt.timedelta(days=2),
    )
    db_session.add(admin_pick)
    db_session.commit()
    db_session.refresh(admin_pick)

    sys.argv = ["generate_weekly_picks.py"]
    script.main()

    db_session.expire_all()
    untouched = db_session.get(AdminPick, admin_pick.id)
    assert untouched is not None
    assert untouched.label == "Hand-picked banker"
    assert untouched.source is None


def test_a_tier_that_cannot_be_filled_leaves_its_previous_row_alone(db_session, script):
    """Only enough matches for the low band this run -- medium and high
    can't be filled. A row already published for "high" from a previous,
    richer week must survive untouched rather than being deleted."""

    _ladder_matches(db_session, LADDER[:15])  # only enough for the low band (see test_betcode_selection.py's ladder)

    stale_high = AdminPick(
        source="system_weekly_high", legs=[], priced=True, label="Last week's high risk pick",
        expires_at=dt.datetime.utcnow() + dt.timedelta(days=5),
    )
    db_session.add(stale_high)
    db_session.commit()
    db_session.refresh(stale_high)

    sys.argv = ["generate_weekly_picks.py"]
    script.main()

    db_session.expire_all()
    assert db_session.query(AdminPick).filter(AdminPick.source == "system_weekly_low").count() == 1
    still_there = db_session.get(AdminPick, stale_high.id)
    assert still_there is not None
    assert still_there.label == "Last week's high risk pick"


def test_dry_run_writes_nothing(db_session, script, capsys):
    _ladder_matches(db_session)

    sys.argv = ["generate_weekly_picks.py", "--dry-run"]
    script.main()

    assert db_session.query(AdminPick).count() == 0
    assert "Dry run -- nothing written" in capsys.readouterr().out
