"""Browsing every available betting outcome across matches, grouped by league.

The per-match endpoints answer "what about this match". This answers the one a
person actually asks when deciding where to look: "where is the best Over 2.5
this week", "show me every correct-score call in La Liga". That needs outcomes
compared across matches, which is a different shape of query entirely.

The query-count test is not a micro-optimisation. The obvious implementation
loops over matches and fetches each one's prediction, and this codebase has
already lost a month of database bandwidth in two nights to exactly that
pattern. A browse page multiplies it by every fixture in a week.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from app.db.models import Match, Prediction, Team
from app.db.session import engine
from app.main import app

client = TestClient(app)

BASE = dt.datetime.utcnow()


def _prediction(match_id: int, *, home=0.55, draw=0.25, away=0.20, dq=1.0, confidence="HIGH") -> Prediction:
    return Prediction(
        match_id=match_id,
        model_version="test",
        home_win=home,
        draw=draw,
        away_win=away,
        over_probabilities={"1.5": 0.78, "2.5": 0.52, "3.5": 0.28},
        btts_yes=0.58,
        btts_no=0.42,
        correct_score_probabilities={"2-1": 0.11, "1-1": 0.09, "1-0": 0.08},
        most_likely_score="2-1",
        most_likely_score_probability=0.11,
        global_outcome_market="Total Goals 1.5",
        global_outcome_selection="Over 1.5",
        global_outcome_probability=0.78,
        confidence=confidence,
        data_quality_score=dq,
        model_agreement_score=0.9,
        explanation={"positive": [], "negative": []},
        model_breakdown={},
    )


@pytest.fixture()
def fixtures(db_session):
    """Two leagues, three upcoming matches, each with a stored prediction."""

    made = {}
    for league, names in {
        "English Premier League": ["Arsenal", "Chelsea", "Spurs", "Everton"],
        "Spanish La Liga": ["Real", "Barca"],
    }.items():
        for name in names:
            team = Team(name=name, league=league, aliases=[])
            db_session.add(team)
            made[name] = team
    db_session.commit()
    for t in made.values():
        db_session.refresh(t)

    pairs = [
        ("English Premier League", "Arsenal", "Chelsea", 1),
        ("English Premier League", "Spurs", "Everton", 2),
        ("Spanish La Liga", "Real", "Barca", 3),
    ]
    matches = []
    for league, home, away, day in pairs:
        m = Match(
            league=league, season="2025-26", date=BASE + dt.timedelta(days=day),
            home_team_id=made[home].id, away_team_id=made[away].id, status="SCHEDULED",
        )
        db_session.add(m)
        matches.append(m)
    db_session.commit()
    for m in matches:
        db_session.refresh(m)
        db_session.add(_prediction(m.id))
    db_session.commit()
    return matches


def _get(auth_headers, **params):
    response = client.get("/api/predictions/outcomes", params=params, headers=auth_headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_requires_authentication():
    assert client.get("/api/predictions/outcomes").status_code == 401


def test_every_market_is_offered_and_grouped_by_league(db_session, auth_headers, fixtures):
    body = _get(auth_headers, days_ahead=7)

    names = {m["market"] for m in body["markets"]}
    assert {"Match Result", "Double Chance", "Both Teams To Score", "Correct Score"} <= names
    assert {"Total Goals 1.5", "Total Goals 2.5", "Total Goals 3.5"} <= names

    leagues = {lg["league"]: lg for lg in body["leagues"]}
    assert set(leagues) == {"English Premier League", "Spanish La Liga"}
    assert leagues["English Premier League"]["matches"] == 2
    assert leagues["Spanish La Liga"]["matches"] == 1
    assert body["total_matches"] == 3


def test_wdl_goals_and_correct_score_are_all_present_for_a_match(db_session, auth_headers, fixtures):
    body = _get(auth_headers, days_ahead=7)
    rows = [o for lg in body["leagues"] for o in lg["outcomes"]]
    first = rows[0]["match_id"]
    for_match = {(o["market"], o["selection"]) for o in rows if o["match_id"] == first}

    assert ("Match Result", "Home Win") in for_match
    assert ("Match Result", "Draw") in for_match
    assert ("Match Result", "Away Win") in for_match
    assert ("Total Goals 2.5", "Over 2.5") in for_match
    assert ("Total Goals 2.5", "Under 2.5") in for_match
    assert ("Correct Score", "2-1") in for_match


def test_mutually_exclusive_selections_sum_to_one(db_session, auth_headers, fixtures):
    """The flag has to mean something: 1X2 must actually sum to ~1, and
    correct score must not claim to."""

    body = _get(auth_headers, days_ahead=7, market="Match Result")
    rows = [o for lg in body["leagues"] for o in lg["outcomes"]]
    first = rows[0]["match_id"]
    total = sum(o["probability"] for o in rows if o["match_id"] == first)
    assert abs(total - 1.0) < 1e-6

    assert body["markets"][0]["mutually_exclusive"] is True

    scores = _get(auth_headers, days_ahead=7, market="Correct Score")
    assert scores["markets"][0]["mutually_exclusive"] is False


def test_double_chance_is_flagged_as_not_summing_to_100_percent(db_session, auth_headers, fixtures):
    """Double Chance's three selections are unions of Match Result (Home/Draw
    = P(Home) + P(Draw)) -- they sum to 2, not 1 -- so the flag that drives
    the Markets page's "these add up to 100%" copy must be False here, the
    same as it already is for Correct Score's truncated top-N."""

    body = _get(auth_headers, days_ahead=7, market="Double Chance")
    assert body["markets"][0]["mutually_exclusive"] is False


def test_filters_narrow_the_result(db_session, auth_headers, fixtures):
    one_league = _get(auth_headers, days_ahead=7, league="Spanish La Liga")
    assert [lg["league"] for lg in one_league["leagues"]] == ["Spanish La Liga"]

    one_market = _get(auth_headers, days_ahead=7, market="Both Teams To Score")
    assert {o["market"] for lg in one_market["leagues"] for o in lg["outcomes"]} == {"Both Teams To Score"}

    likely = _get(auth_headers, days_ahead=7, min_probability=0.7)
    assert all(o["probability"] >= 0.7 for lg in likely["leagues"] for o in lg["outcomes"])
    assert likely["total_outcomes"] < _get(auth_headers, days_ahead=7)["total_outcomes"]

    none_match = _get(auth_headers, days_ahead=7, confidence="LOW")
    assert none_match["total_outcomes"] == 0


def test_outcomes_are_ordered_by_probability_within_a_league(db_session, auth_headers, fixtures):
    body = _get(auth_headers, days_ahead=7)
    for lg in body["leagues"]:
        probs = [o["probability"] for o in lg["outcomes"]]
        assert probs == sorted(probs, reverse=True)


def test_thin_data_hides_correct_score(db_session, auth_headers, fixtures):
    """Correct score needs ten matches of history. A prediction generated from
    less must not start offering it just because it is being re-read."""

    # 0.7 -> seven matches of history: past the five every other market needs,
    # short of the ten correct score does. 0.4 would prove nothing, because it
    # is below the threshold for all of them.
    thin = fixtures[0]
    db_session.query(Prediction).filter_by(match_id=thin.id).delete()
    db_session.add(_prediction(thin.id, dq=0.7))
    db_session.commit()

    body = _get(auth_headers, days_ahead=7)
    rows = [o for lg in body["leagues"] for o in lg["outcomes"] if o["match_id"] == thin.id]
    assert rows, "the match should still offer its other markets"
    assert not [o for o in rows if o["market"] == "Correct Score"]


def test_matches_without_a_prediction_are_skipped_not_generated(db_session, auth_headers, fixtures):
    bare = fixtures[-1]
    db_session.query(Prediction).filter_by(match_id=bare.id).delete()
    db_session.commit()

    body = _get(auth_headers, days_ahead=7)
    assert bare.id not in {o["match_id"] for lg in body["leagues"] for o in lg["outcomes"]}
    # ...and nothing was written to fill the gap.
    assert db_session.query(Prediction).filter_by(match_id=bare.id).count() == 0


def test_query_count_does_not_grow_with_the_number_of_matches(db_session, auth_headers, fixtures):
    """The guard that matters. A per-match fetch is what cost this project a
    month of database egress in two nights."""

    statements: list[str] = []

    def record(conn, cursor, statement, params, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        small = _get(auth_headers, days_ahead=7)
        baseline = len(statements)

        # Triple the fixtures, then measure again.
        statements.clear()
        teams = db_session.query(Team).all()
        for i in range(6):
            m = Match(
                league="English Premier League", season="2025-26",
                date=BASE + dt.timedelta(days=4, hours=i),
                home_team_id=teams[i % len(teams)].id,
                away_team_id=teams[(i + 1) % len(teams)].id,
                status="SCHEDULED",
            )
            db_session.add(m)
            db_session.flush()
            db_session.add(_prediction(m.id))
        db_session.commit()
        statements.clear()

        larger = _get(auth_headers, days_ahead=7)
        grown = len(statements)
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert larger["total_matches"] > small["total_matches"], "the fixture setup didn't add matches"
    assert grown <= baseline, (
        f"queries grew from {baseline} to {grown} as matches went from "
        f"{small['total_matches']} to {larger['total_matches']} -- this is per-match fetching"
    )
