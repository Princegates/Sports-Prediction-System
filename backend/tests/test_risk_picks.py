"""The daily Low / Medium / High risk slips (app.risk_picks): 10-15 legs each,
defined by per-leg probability bands, unpriced, one row per tier, replaced
when a new day starts or the slip's first match is about to kick off."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app.db.models import AdminPick, Match, Prediction
from app.main import app
from app.risk_picks import MAX_LEGS, MIN_LEGS, SOURCE_PREFIX, TIERS, generate_daily
from app.timeutil import utcnow
from tests.test_select_band_legs import add_matches

client = TestClient(app)

BANDS = {tier: (low, high) for tier, low, high in TIERS}


@pytest.fixture()
def plenty(db_session):
    """Enough matches for every tier to fill from its own set: 12 clear
    favourites (Low band), 36 moderate ones (High band: Match Result 0.62),
    6 open ones."""

    add_matches(db_session, 12, 0.75, 0.15, 0.10)
    add_matches(db_session, 36, 0.62, 0.22, 0.16, start=100)
    add_matches(db_session, 6, 0.45, 0.28, 0.27, start=200)


def rows(db):
    return {p.source: p for p in db.query(AdminPick).filter(AdminPick.source.startswith(SOURCE_PREFIX)).all()}


def leg_probabilities(db, pick):
    from app.betcode.selection import resolve_legs_unpriced

    legs, warnings = resolve_legs_unpriced(db, [(l["match_id"], l["market"], l["selection"]) for l in pick.legs])
    assert warnings == []
    return [leg.model_probability for leg in legs]


def test_publishes_one_slip_per_tier_of_ten_to_fifteen_unpriced_legs(db_session, plenty):
    generate_daily(db_session)
    db_session.commit()

    picks = rows(db_session)
    assert set(picks) == {"system_daily_low", "system_daily_medium", "system_daily_high"}
    for tier, pick in ((p.source.removeprefix(SOURCE_PREFIX), p) for p in picks.values()):
        assert MIN_LEGS <= len(pick.legs) <= MAX_LEGS
        assert pick.priced is False and pick.created_by_user_id is None
        assert pick.label == f"{tier.capitalize()} Risk -- Daily Accumulator"
        assert pick.expires_at > utcnow()


def test_every_leg_of_a_tier_sits_in_that_tiers_band(db_session, plenty):
    generate_daily(db_session)
    db_session.commit()

    for source, pick in rows(db_session).items():
        low, high = BANDS[source.removeprefix(SOURCE_PREFIX)]
        assert all(low <= p < high for p in leg_probabilities(db_session, pick)), source


def test_the_three_slips_are_built_from_different_matches_when_there_are_enough(db_session, plenty):
    generate_daily(db_session)
    db_session.commit()

    sets = [{l["match_id"] for l in p.legs} for p in rows(db_session).values()]
    assert not (sets[0] & sets[1]) and not (sets[0] & sets[2]) and not (sets[1] & sets[2])


def test_running_again_the_same_day_changes_nothing(db_session, plenty):
    generate_daily(db_session)
    db_session.commit()
    before = {s: (p.id, p.legs) for s, p in rows(db_session).items()}

    lines = generate_daily(db_session)
    db_session.commit()

    assert {s: (p.id, p.legs) for s, p in rows(db_session).items()} == before
    assert all("current -- kept" in line for line in lines)


def test_a_slip_is_replaced_once_its_first_match_is_about_to_kick_off(db_session, plenty):
    """A pending pick vanishes from the page when any match finishes, so a
    slip can't be left running past its first kickoff."""

    generate_daily(db_session)
    db_session.commit()
    old = rows(db_session)["system_daily_low"]
    old_id = old.id
    first = db_session.get(Match, old.legs[0]["match_id"])
    first.date = utcnow() + dt.timedelta(minutes=5)
    db_session.commit()

    generate_daily(db_session)
    db_session.commit()

    new = rows(db_session)["system_daily_low"]
    assert new.id != old_id
    assert db_session.get(Match, first.id).id not in {l["match_id"] for l in new.legs}


def test_a_slip_is_replaced_when_a_new_day_starts(db_session, plenty):
    generate_daily(db_session)
    db_session.commit()
    before = {s: p.id for s, p in rows(db_session).items()}

    generate_daily(db_session, now=utcnow() + dt.timedelta(days=1))
    db_session.commit()

    assert all(rows(db_session)[s].id != before[s] for s in before)
    assert len(rows(db_session)) == 3


def test_a_replaced_slip_starts_pending_not_carrying_the_old_settlement(db_session, plenty):
    generate_daily(db_session)
    db_session.commit()
    old = rows(db_session)["system_daily_high"]
    old.result, old.leg_results, old.settled_at = "won", ["won"] * len(old.legs), utcnow()
    db_session.commit()

    generate_daily(db_session, force=True)
    db_session.commit()

    new = rows(db_session)["system_daily_high"]
    assert (new.result, new.leg_results, new.settled_at) == ("pending", None, None)


def test_force_rebuilds_even_a_current_slip(db_session, plenty):
    generate_daily(db_session)
    db_session.commit()
    before = {s: p.id for s, p in rows(db_session).items()}

    generate_daily(db_session, force=True)
    db_session.commit()

    assert all(rows(db_session)[s].id != before[s] for s in before)


def test_a_tier_that_cannot_be_filled_keeps_its_previous_slip(db_session, plenty):
    generate_daily(db_session)
    db_session.commit()
    before = rows(db_session)["system_daily_high"]
    before_id, before_legs = before.id, before.legs

    # Nothing left to build from -- the old slip stays rather than vanishing.
    db_session.query(Prediction).delete()
    db_session.commit()
    lines = generate_daily(db_session, now=utcnow() + dt.timedelta(days=1))
    db_session.commit()

    kept = rows(db_session)["system_daily_high"]
    assert (kept.id, kept.legs) == (before_id, before_legs)
    assert any("[high] skipped" in line and "kept the previous slip" in line for line in lines)


def test_with_too_few_matches_nothing_is_published_at_all(db_session):
    add_matches(db_session, 4, 0.75, 0.15, 0.10)

    lines = generate_daily(db_session)
    db_session.commit()

    assert rows(db_session) == {}
    assert all("skipped" in line for line in lines) and len(lines) == 3


def test_a_quiet_stretch_widens_the_window_until_there_are_enough_matches(db_session):
    """Nothing in the next day or two, but plenty three days out."""

    matches = add_matches(db_session, 14, 0.75, 0.15, 0.10, hours_ahead=60)
    assert all(m.date > utcnow() + dt.timedelta(days=2) for m in matches)

    generate_daily(db_session)
    db_session.commit()

    assert "system_daily_low" in rows(db_session)
    assert "next 3 day(s)" in rows(db_session)["system_daily_low"].note


def test_a_dry_run_writes_nothing(db_session, plenty):
    lines = generate_daily(db_session, dry_run=True)
    db_session.commit()

    assert rows(db_session) == {}
    assert any(line.startswith("[low]") for line in lines)


def test_slips_from_other_sources_are_never_touched(db_session, plenty):
    other = AdminPick(
        source="system_weekly_low", legs=[{"match_id": 1, "market": "Match Result", "selection": "Home Win"}],
        priced=True, label="weekly", expires_at=utcnow() + dt.timedelta(days=3),
    )
    hand = AdminPick(
        source=None, legs=[{"match_id": 2, "market": "Match Result", "selection": "Home Win"}],
        priced=False, label="by hand", expires_at=utcnow() + dt.timedelta(days=3),
    )
    db_session.add_all([other, hand])
    db_session.commit()

    generate_daily(db_session, force=True)
    db_session.commit()

    assert {p.label for p in db_session.query(AdminPick).filter(AdminPick.source.is_(None)).all()} == {"by hand"}
    assert db_session.query(AdminPick).filter(AdminPick.source == "system_weekly_low").count() == 1


def test_the_slips_reach_members_through_the_ai_picks_endpoint(db_session, auth_headers, plenty):
    generate_daily(db_session)
    db_session.commit()

    body = client.get("/api/predictions/admin-picks", headers=auth_headers).json()

    daily = {p["source"]: p for p in body if (p["source"] or "").startswith(SOURCE_PREFIX)}
    assert set(daily) == {"system_daily_low", "system_daily_medium", "system_daily_high"}
    for pick in daily.values():
        assert not pick["locked"] and MIN_LEGS <= len(pick["legs"]) <= MAX_LEGS and pick["priced"] is False
