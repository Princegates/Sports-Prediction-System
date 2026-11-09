"""select_band_legs: a slip whose every leg sits in one probability band --
what makes a Low, a Medium and a High risk slip genuinely different."""

from __future__ import annotations

import datetime as dt

import pytest

from app.betcode.selection import select_band_legs
from app.db.models import Match, Prediction, Team
from app.timeutil import utcnow

LEAGUE = "English Premier League"


def _prediction(match_id: int, home: float, draw: float, away: float) -> Prediction:
    """Outcome probabilities this produces (checked against the registry):
    Match Result -> the best of home/draw/away, Double Chance -> the best
    pair sum, Total Goals 1.5 -> 0.80 whatever the 1X2 says."""

    return Prediction(
        match_id=match_id, model_version="test", home_win=home, draw=draw, away_win=away,
        over_probabilities={"2.5": 0.60, "1.5": 0.80}, btts_yes=0.55, btts_no=0.45,
        correct_score_probabilities={"2-1": 0.11}, most_likely_score="2-1", most_likely_score_probability=0.11,
        global_outcome_market="Match Result", global_outcome_selection="Home Win", global_outcome_probability=home,
        confidence="HIGH", data_quality_score=1.0, model_agreement_score=0.9,
        explanation={"positive": [], "negative": []}, model_breakdown={},
    )


def add_matches(db, count, home, draw, away, *, start=0, league=LEAGUE, hours_ahead=3):
    created = []
    for i in range(start, start + count):
        h = Team(name=f"H{league[:3]}{home}{i}", league=league, aliases=[])
        a = Team(name=f"A{league[:3]}{home}{i}", league=league, aliases=[])
        db.add_all([h, a])
        db.commit()
        match = Match(
            league=league, season="2025-26", date=utcnow() + dt.timedelta(hours=hours_ahead + (i % 18)),
            home_team_id=h.id, away_team_id=a.id, status="SCHEDULED",
        )
        db.add(match)
        db.commit()
        db.add(_prediction(match.id, home, draw, away))
        db.commit()
        created.append(match)
    return created


def band(db, low, high, **kw):
    kw.setdefault("min_legs", 10)
    kw.setdefault("max_legs", 15)
    kw.setdefault("days_ahead", 2)
    return select_band_legs(db, min_probability=low, max_probability=high, **kw)


def test_every_leg_sits_inside_the_band_and_matches_are_not_repeated(db_session):
    add_matches(db_session, 12, 0.75, 0.15, 0.10)  # DC 0.90, MR 0.75, TG1.5 0.80

    medium = band(db_session, 0.70, 0.85)

    assert len(medium.legs) >= 10
    assert all(0.70 <= leg.model_probability < 0.85 for leg in medium.legs)
    assert len({leg.match_id for leg in medium.legs}) == len(medium.legs)


def test_low_medium_and_high_come_out_genuinely_different(db_session):
    add_matches(db_session, 12, 0.75, 0.15, 0.10)
    add_matches(db_session, 12, 0.62, 0.22, 0.16, start=100)  # MR 0.62, DC 0.84, TG 0.80

    low = band(db_session, 0.85, 1.01)
    medium = band(db_session, 0.70, 0.85)
    high = band(db_session, 0.55, 0.70)

    assert min(leg.model_probability for leg in low.legs) >= 0.85
    assert all(0.70 <= leg.model_probability < 0.85 for leg in medium.legs)
    assert all(0.55 <= leg.model_probability < 0.70 for leg in high.legs)
    assert low.combined_probability > medium.combined_probability > high.combined_probability


def test_a_slip_is_never_longer_than_max_legs(db_session):
    add_matches(db_session, 25, 0.75, 0.15, 0.10)
    assert len(band(db_session, 0.70, 0.85).legs) == 15


def test_markets_are_spread_when_there_is_variety_to_spread(db_session):
    add_matches(db_session, 10, 0.75, 0.15, 0.10)  # medium band: MR 0.75 and TG1.5 0.80
    add_matches(db_session, 10, 0.62, 0.22, 0.16, start=100)  # medium band: DC 0.84 and TG1.5 0.80

    markets = {leg.market for leg in band(db_session, 0.70, 0.85).legs}

    assert len(markets) >= 3


def test_a_band_with_only_one_market_still_fills_the_slip(db_session):
    """Only Match Result fits this band -- the family cap must not hold the
    slip to 3 legs when there's no other market to vary it with."""

    add_matches(db_session, 12, 0.62, 0.22, 0.16)

    legs = band(db_session, 0.55, 0.70).legs

    assert len(legs) == 12
    assert {leg.market for leg in legs} == {"Match Result"}


def test_a_lopsided_pool_is_topped_up_past_the_cap_to_reach_the_minimum(db_session):
    """Nine Match Result legs and a single Double Chance one: the cap (8 each
    between two families) stops at 9 -- one short of the minimum -- so the
    last slot is filled from the larger family instead of leaving the slip
    short."""

    add_matches(db_session, 9, 0.62, 0.22, 0.16)  # Match Result 0.62 is the band's only leg
    add_matches(db_session, 1, 0.34, 0.33, 0.33, start=100)  # Double Chance 0.67 is its only leg

    legs = band(db_session, 0.55, 0.70).legs

    assert len(legs) == 10
    assert sum(leg.market == "Double Chance" for leg in legs) == 1


def test_matches_a_caller_excludes_are_left_alone(db_session):
    matches = add_matches(db_session, 20, 0.75, 0.15, 0.10)
    excluded = frozenset(m.id for m in matches[:8])

    legs = band(db_session, 0.70, 0.85, exclude_match_ids=excluded).legs

    assert legs and not ({leg.match_id for leg in legs} & excluded)


def test_too_few_in_band_is_reported_not_padded_with_the_wrong_legs(db_session):
    add_matches(db_session, 4, 0.75, 0.15, 0.10)

    result = band(db_session, 0.55, 0.70)

    assert result.legs == [] and "between 55% and 70%" in result.warnings[0]


def test_a_match_already_started_is_never_used(db_session):
    matches = add_matches(db_session, 12, 0.75, 0.15, 0.10)
    started = matches[0]
    started.date = utcnow() - dt.timedelta(hours=1)
    db_session.commit()

    assert started.id not in {leg.match_id for leg in band(db_session, 0.70, 0.85).legs}


def test_a_very_long_exclusion_list_is_applied_without_sending_it_to_the_database(db_session):
    """More than SQL_EXCLUDE_LIMIT ids are filtered in Python (a single NOT IN
    that long exceeds PostgreSQL's parameter limit); the result must be the
    same as a short list would give."""

    from app.betcode.selection import SQL_EXCLUDE_LIMIT

    matches = add_matches(db_session, 14, 0.75, 0.15, 0.10)
    banned = {m.id for m in matches[:4]}
    padding = set(range(1_000_000, 1_000_000 + SQL_EXCLUDE_LIMIT + 200))  # ids that match nothing

    short = band(db_session, 0.70, 1.01, exclude_match_ids=frozenset(banned))
    long = band(db_session, 0.70, 1.01, exclude_match_ids=frozenset(banned | padding))

    assert len(banned | padding) > SQL_EXCLUDE_LIMIT
    assert {leg.match_id for leg in long.legs}.isdisjoint(banned)
    assert [leg.match_id for leg in long.legs] == [leg.match_id for leg in short.legs]
