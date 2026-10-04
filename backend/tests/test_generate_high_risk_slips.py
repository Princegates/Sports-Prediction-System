"""scripts/generate_high_risk_slips.py publishes three weekly "jackpot"
accumulators (10-20 legs each, pooled across every league, each required
to clear 50x combined odds) onto the same admin_picks table
generate_weekly_picks.py and a human admin both write to -- distinguished
only by AdminPick.source, so re-running it replaces each slip's own row
instead of piling up duplicates, and never touches a row an admin or the
regular weekly tiers created.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from pathlib import Path

import pytest

from app.db.models import AdminPick, Match, MatchOdds, Prediction, Team

BASE = dt.datetime.utcnow() + dt.timedelta(hours=6)

# 60 matches on a wide, evenly-stepped odds ladder: enough for three
# non-overlapping 10-to-20-leg slips (up to 60 legs total) each able to
# clear 50x without running out of candidates.
LADDER = [round(1.05 + 0.03 * i, 3) for i in range(60)]


@pytest.fixture()
def script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "generate_high_risk_slips.py"
    spec = importlib.util.spec_from_file_location("generate_high_risk_slips_under_test", path)
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


def test_generates_three_distinct_slips_each_clearing_the_floor(db_session, script):
    _ladder_matches(db_session)

    sys.argv = ["generate_high_risk_slips.py"]
    script.main()

    db_session.expire_all()
    picks = {p.source: p for p in db_session.query(AdminPick).all()}
    assert set(picks) == {"system_weekly_jackpot_1", "system_weekly_jackpot_2", "system_weekly_jackpot_3"}

    seen_match_ids: set[int] = set()
    for source, pick in picks.items():
        assert 10 <= len(pick.legs) <= 20
        assert pick.priced is True
        assert pick.booking_code is None
        combined = _stored_combined_odds(pick, db_session)
        assert combined >= 50.0, f"{source}: {combined} below the 50x floor"

        match_ids = {leg["match_id"] for leg in pick.legs}
        assert not (match_ids & seen_match_ids), f"{source} reuses a match an earlier slip already used"
        seen_match_ids |= match_ids


def test_rerun_replaces_the_same_row_rather_than_duplicating(db_session, script):
    _ladder_matches(db_session)
    sys.argv = ["generate_high_risk_slips.py"]

    script.main()
    db_session.expire_all()
    first_id = db_session.query(AdminPick).filter(AdminPick.source == "system_weekly_jackpot_1").one().id

    script.main()
    db_session.expire_all()
    rows = db_session.query(AdminPick).filter(AdminPick.source == "system_weekly_jackpot_1").all()
    assert len(rows) == 1
    assert rows[0].id == first_id


def test_a_slip_that_cannot_be_filled_leaves_its_previous_row_alone(db_session, script):
    """Only enough matches for one jackpot slip this run (26, verified above
    -- see test_betcode_selection.py's ladder -- to leave fewer than the
    10-leg minimum once slip 1 has taken what it needs) -- the second and
    third can't be filled. A row already published for slip 3 from a
    previous, richer week must survive untouched rather than being deleted."""

    _ladder_matches(db_session, LADDER[:26])

    stale = AdminPick(
        source="system_weekly_jackpot_3", legs=[], priced=True, label="Last week's jackpot #3",
        expires_at=dt.datetime.utcnow() + dt.timedelta(days=5),
    )
    db_session.add(stale)
    db_session.commit()
    db_session.refresh(stale)

    sys.argv = ["generate_high_risk_slips.py"]
    script.main()

    db_session.expire_all()
    assert db_session.query(AdminPick).filter(AdminPick.source == "system_weekly_jackpot_1").count() == 1
    still_there = db_session.get(AdminPick, stale.id)
    assert still_there is not None
    assert still_there.label == "Last week's jackpot #3"


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

    sys.argv = ["generate_high_risk_slips.py"]
    script.main()

    db_session.expire_all()
    untouched = db_session.get(AdminPick, admin_pick.id)
    assert untouched is not None
    assert untouched.label == "Hand-picked banker"
    assert untouched.source is None


def test_dry_run_writes_nothing(db_session, script, capsys):
    _ladder_matches(db_session)

    sys.argv = ["generate_high_risk_slips.py", "--dry-run"]
    script.main()

    assert db_session.query(AdminPick).count() == 0
    assert "Dry run -- nothing written" in capsys.readouterr().out
