"""Public-page data: the combined landing call, fixture facts, the public
pick of the day, league filtering, caching, and the structured price."""

from __future__ import annotations

import datetime as dt

from fastapi.testclient import TestClient

from app import app_settings
from app.db.models import Match, Prediction, Team
from app.main import app
from app.timeutil import utcnow

client = TestClient(app)


def _team(db, name, league="English Premier League", provider_id=None):
    t = Team(name=name, league=league, aliases=[], api_football_team_id=provider_id)
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def _match(db, home, away, *, hours, status="SCHEDULED", score=None, league="English Premier League"):
    m = Match(league=league, season="2526", date=utcnow() + dt.timedelta(hours=hours),
              home_team_id=home.id, away_team_id=away.id, status=status)
    if score:
        m.home_score, m.away_score = score
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


def _predict(db, match, *, confidence, prob, selection="Home Win"):
    db.add(Prediction(
        match_id=match.id, model_version="t", home_win=prob, draw=(1 - prob) / 2, away_win=(1 - prob) / 2,
        over_probabilities={"2.5": 0.5}, btts_yes=0.5, btts_no=0.5, correct_score_probabilities={"1-0": 0.1},
        most_likely_score="1-0", most_likely_score_probability=0.1, global_outcome_market="Match Result",
        global_outcome_selection=selection, global_outcome_probability=prob, confidence=confidence,
        data_quality_score=1.0, model_agreement_score=0.9, explanation={"positive": [], "negative": []}, model_breakdown={},
    ))
    db.commit()


def test_pick_of_the_day_is_the_strongest_high_confidence_call_soon(db_session):
    a, b, c, d = (_team(db_session, n) for n in ("Arsenal", "Chelsea", "Everton", "Fulham"))
    soon_weaker = _match(db_session, a, b, hours=5)
    soon_strong = _match(db_session, c, d, hours=8)
    later = _match(db_session, b, a, hours=60)
    _predict(db_session, soon_weaker, confidence="HIGH", prob=0.71)
    _predict(db_session, soon_strong, confidence="HIGH", prob=0.83)
    _predict(db_session, later, confidence="HIGH", prob=0.95)  # outside the 36h window

    body = client.get("/api/public/pick-of-the-day").json()
    assert body["has_data"] is True
    assert body["fixture"]["match_id"] == soon_strong.id
    assert (body["selection"], body["probability"]) == ("Home Win", 0.83)

    # Its fixture page shows it; another fixture's page doesn't.
    assert client.get(f"/api/public/fixtures/{soon_strong.id}").json()["pick_of_the_day"]["probability"] == 0.83
    assert client.get(f"/api/public/fixtures/{soon_weaker.id}").json()["pick_of_the_day"] is None


def test_no_high_confidence_fixture_means_no_pick(db_session):
    a, b = _team(db_session, "Arsenal"), _team(db_session, "Chelsea")
    _predict(db_session, _match(db_session, a, b, hours=5), confidence="MEDIUM", prob=0.6)
    assert client.get("/api/public/pick-of-the-day").json() == {
        "has_data": False, "fixture": None, "market": None, "selection": None, "probability": None,
    }


def test_fixture_page_carries_form_and_head_to_head_but_no_prediction(db_session):
    a, b, c = _team(db_session, "Arsenal", provider_id=42), _team(db_session, "Chelsea"), _team(db_session, "Everton")
    _match(db_session, a, b, hours=-24 * 30, status="FINISHED", score=(2, 1))
    _match(db_session, c, a, hours=-24 * 7, status="FINISHED", score=(0, 0))
    _match(db_session, b, c, hours=-24 * 3, status="FINISHED", score=(1, 3))
    upcoming = _match(db_session, a, b, hours=20)
    _predict(db_session, upcoming, confidence="MEDIUM", prob=0.55)

    body = client.get(f"/api/public/fixtures/{upcoming.id}").json()
    assert [(r["opponent"], r["venue"], r["result"]) for r in body["home_form"]] == [("Everton", "A", "D"), ("Chelsea", "H", "W")]
    assert [r["result"] for r in body["away_form"]] == ["L", "L"]
    assert [(h["home_team"], h["home_score"], h["away_score"]) for h in body["head_to_head"]] == [("Arsenal", 2, 1)]
    assert body["home_logo_url"].endswith("/42.png") and body["away_logo_url"] is None
    assert "selection" not in body and "probability" not in body


def test_fixtures_filter_by_league_and_sort_by_confidence(db_session):
    a, b = _team(db_session, "Arsenal"), _team(db_session, "Chelsea")
    r, s = _team(db_session, "Real Madrid", league="Spanish La Liga"), _team(db_session, "Sevilla", league="Spanish La Liga")
    low = _match(db_session, a, b, hours=2)
    high = _match(db_session, b, a, hours=10)
    liga = _match(db_session, r, s, hours=5, league="Spanish La Liga")
    _predict(db_session, low, confidence="LOW", prob=0.4)
    _predict(db_session, high, confidence="HIGH", prob=0.7)
    _predict(db_session, liga, confidence="MEDIUM", prob=0.5)

    by_time = [f["match_id"] for f in client.get("/api/public/fixtures?days_ahead=2&limit=5").json()]
    assert by_time == [low.id, liga.id, high.id]
    best = [f["match_id"] for f in client.get("/api/public/fixtures?days_ahead=2&limit=5&sort=confidence").json()]
    assert best == [high.id, liga.id, low.id]
    only_liga = client.get("/api/public/fixtures?days_ahead=2&league=Spanish%20La%20Liga").json()
    assert [f["match_id"] for f in only_liga] == [liga.id]


def test_landing_bundles_the_home_page_in_one_cached_call(db_session):
    response = client.get("/api/public/landing")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"branding", "stats", "track_record", "admin_pick_record", "fixtures", "pick_of_the_day", "teaser_pick"}
    assert response.headers["cache-control"].startswith("public, max-age=300")
    # Authenticated, per-user routes are never marked cacheable.
    assert "public" not in client.get("/api/auth/me").headers.get("cache-control", "")


def test_structured_price_beats_the_free_text_label(db_session):
    app_settings.set_values(db_session, {"pricing_label": "GHC 20/5 days acess"})
    assert client.get("/api/public/branding").json()["pricing_label"] == "GHC 20/5 days acess"

    app_settings.set_values(db_session, {"price_amount": 20, "price_days": 5})
    body = client.get("/api/public/branding").json()
    assert body["pricing_label"] == "GH₵20 for 5 days"
    assert body["price_per_day"] == "about GH₵4 a day"


def test_the_mistyped_production_label_is_structured_once(db_session):
    from app.db.migrate import _structure_mistyped_price_label
    from app.db.session import engine

    app_settings.set_values(db_session, {"pricing_label": "GHC 20/5 days acess"})
    _structure_mistyped_price_label(engine)
    _structure_mistyped_price_label(engine)  # idempotent

    db_session.expire_all()
    values = app_settings.all_values(db_session)
    assert app_settings.price_display(values) == ("GH₵20 for 5 days", "about GH₵4 a day")
    assert values["pricing_label"] == ""
