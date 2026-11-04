"""scripts/generate_weekly_picks.py publishes the week's three system
accumulators (Low / Medium / High risk, 10-15 legs each) onto the same
admin_picks table a human admin curates by hand -- distinguished only by
AdminPick.source, so re-running it replaces each tier's own row instead of
piling up duplicates, and never touches a row an admin created themselves.

The tiers are per-leg probability bands built by app.risk_picks, not
combined-odds bands: the odds version needed a captured bookmaker price on
every leg and published nothing the week none were captured.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path

import pytest

from app.db.models import AdminPick, Prediction
from app.risk_picks import CADENCES, MAX_LEGS, MIN_LEGS, TIERS, generate_weekly
from app.timeutil import utcnow
from tests.test_select_band_legs import add_matches

BANDS = {tier: (low, high) for tier, low, high in TIERS}
PREFIX = "system_weekly_"


@pytest.fixture()
def script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "generate_weekly_picks.py"
    spec = importlib.util.spec_from_file_location("generate_weekly_picks_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def plenty(db_session):
    add_matches(db_session, 12, 0.75, 0.15, 0.10)
    add_matches(db_session, 36, 0.62, 0.22, 0.16, start=100)
    add_matches(db_session, 6, 0.45, 0.28, 0.27, start=200)


def weekly(db):
    return {p.source: p for p in db.query(AdminPick).filter(AdminPick.source.in_([f"{PREFIX}{t}" for t, _, _ in TIERS])).all()}


def run(script, **overrides):
    import sys

    argv = ["generate_weekly_picks.py", *overrides.get("args", [])]
    old = sys.argv
    sys.argv = argv
    try:
        script.main()
    finally:
        sys.argv = old


def test_generates_all_three_tiers_as_unpriced_ten_to_fifteen_leg_slips(db_session, plenty, script):
    run(script)

    picks = weekly(db_session)
    assert set(picks) == {f"{PREFIX}low", f"{PREFIX}medium", f"{PREFIX}high"}
    for source, pick in picks.items():
        tier = source.removeprefix(PREFIX)
        assert MIN_LEGS <= len(pick.legs) <= MAX_LEGS
        assert pick.priced is False and pick.created_by_user_id is None
        assert pick.label == f"{tier.capitalize()} Risk -- Weekly Accumulator"
        assert "week of" in pick.note


def test_every_leg_sits_in_its_tiers_probability_band(db_session, plenty, script):
    from app.betcode.selection import resolve_legs_unpriced

    run(script)

    for source, pick in weekly(db_session).items():
        low, high = BANDS[source.removeprefix(PREFIX)]
        legs, warnings = resolve_legs_unpriced(db_session, [(l["match_id"], l["market"], l["selection"]) for l in pick.legs])
        assert warnings == [] and all(low <= leg.model_probability < high for leg in legs), source


def test_it_publishes_even_when_no_bookmaker_odds_exist_at_all(db_session, plenty, script):
    """The failure this replaced: no MatchOdds -> 'Only 0 qualifying matches'
    for every tier and nothing published."""

    from app.db.models import MatchOdds

    assert db_session.query(MatchOdds).count() == 0
    run(script)
    assert len(weekly(db_session)) == 3


def test_rerunning_in_the_same_week_leaves_the_slips_alone(db_session, plenty, script):
    run(script)
    before = {s: (p.id, p.legs) for s, p in weekly(db_session).items()}

    run(script)

    assert {s: (p.id, p.legs) for s, p in weekly(db_session).items()} == before


def test_force_rebuilds_the_week_even_when_current(db_session, plenty, script):
    run(script)
    before = {s: p.id for s, p in weekly(db_session).items()}

    run(script, args=["--force"])

    after = weekly(db_session)
    assert len(after) == 3 and all(after[s].id != before[s] for s in before)


def test_a_stale_odds_band_slip_from_the_old_script_is_replaced(db_session, plenty, script):
    """Last week's priced row (what the old script wrote) is replaced by a
    probability-band slip on the same source -- one row per tier, not two."""

    old = AdminPick(
        source=f"{PREFIX}low", legs=[{"match_id": 1, "market": "Match Result", "selection": "Home Win"}],
        priced=True, label="Low Risk -- Weekly 10-Leg Accumulator (5-10 odds)", booking_code="OLDCODE",
        booking_code_bookmaker="SportyBet", expires_at=utcnow() + dt.timedelta(days=1),
        created_at=utcnow() - dt.timedelta(days=8),
    )
    db_session.add(old)
    db_session.commit()

    run(script)

    rows = db_session.query(AdminPick).filter(AdminPick.source == f"{PREFIX}low").all()
    assert len(rows) == 1
    assert rows[0].priced is False and rows[0].booking_code is None and "5-10 odds" not in rows[0].label


def test_admin_authored_picks_and_jackpot_slips_are_never_touched(db_session, plenty, script):
    hand = AdminPick(source=None, legs=[{"match_id": 2, "market": "Match Result", "selection": "Home Win"}],
                     priced=False, label="by hand", expires_at=utcnow() + dt.timedelta(days=3))
    jackpot = AdminPick(source="system_weekly_jackpot_1", legs=[{"match_id": 3, "market": "Match Result", "selection": "Home Win"}],
                        priced=True, label="jackpot", expires_at=utcnow() + dt.timedelta(days=3))
    db_session.add_all([hand, jackpot])
    db_session.commit()

    run(script, args=["--force"])

    assert db_session.query(AdminPick).filter(AdminPick.label == "by hand").count() == 1
    assert db_session.query(AdminPick).filter(AdminPick.source == "system_weekly_jackpot_1").count() == 1


def test_a_tier_that_cannot_be_filled_leaves_its_previous_row_alone(db_session, plenty, script):
    run(script)
    before = weekly(db_session)[f"{PREFIX}high"]
    before_id, before_legs = before.id, before.legs

    db_session.query(Prediction).delete()
    db_session.commit()
    run(script, args=["--force"])

    kept = weekly(db_session)[f"{PREFIX}high"]
    assert (kept.id, kept.legs) == (before_id, before_legs)


def test_a_dry_run_writes_nothing(db_session, plenty, script, capsys):
    run(script, args=["--dry-run"])

    assert weekly(db_session) == {}
    assert "Dry run -- nothing written." in capsys.readouterr().out


def test_the_weekly_cadence_looks_further_ahead_than_the_daily_one(db_session, script):
    """Matches six days out: beyond the daily window, inside the weekly one."""

    add_matches(db_session, 14, 0.75, 0.15, 0.10, hours_ahead=6 * 24)

    from app.risk_picks import generate_daily

    generate_daily(db_session)
    db_session.commit()
    assert db_session.query(AdminPick).filter(AdminPick.source.startswith("system_daily_")).count() == 0

    generate_weekly(db_session)
    db_session.commit()
    assert f"{PREFIX}low" in weekly(db_session)


def test_the_weekly_period_is_the_iso_week_and_the_daily_one_the_day():
    monday = dt.datetime(2026, 11, 2, 9, 0)
    sunday = dt.datetime(2026, 11, 8, 23, 0)
    next_monday = dt.datetime(2026, 11, 9, 0, 5)

    assert CADENCES["weekly"].period(monday) == CADENCES["weekly"].period(sunday)
    assert CADENCES["weekly"].period(sunday) != CADENCES["weekly"].period(next_monday)
    assert CADENCES["daily"].period(monday) != CADENCES["daily"].period(sunday)


def test_a_slip_from_last_week_is_replaced_in_a_new_week(db_session, plenty):
    generate_weekly(db_session)
    db_session.commit()
    before = {s: p.id for s, p in weekly(db_session).items()}

    generate_weekly(db_session, now=utcnow() + dt.timedelta(days=7))
    db_session.commit()

    after = weekly(db_session)
    assert len(after) == 3 and all(after[s].id != before[s] for s in before)
