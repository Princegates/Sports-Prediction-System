"""Public-page data: the combined landing call, fixture facts, the three
free picks, league filtering, caching, and the structured price."""

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


def _predict(db, match, *, confidence, prob, selection="Home Win", over15=None, draw=None):
    draw = (1 - prob) / 2 if draw is None else draw
    over = {"2.5": 0.5} if over15 is None else {"1.5": over15, "2.5": 0.5}
    db.add(Prediction(
        match_id=match.id, model_version="t", home_win=prob, draw=draw, away_win=1 - prob - draw,
        over_probabilities=over, btts_yes=0.5, btts_no=0.5, correct_score_probabilities={"1-0": 0.1},
        most_likely_score="1-0", most_likely_score_probability=0.1, global_outcome_market="Match Result",
        global_outcome_selection=selection, global_outcome_probability=prob, confidence=confidence,
        data_quality_score=1.0, model_agreement_score=0.9, explanation={"positive": [], "negative": []}, model_breakdown={},
    ))
    db.commit()


def test_three_free_picks_one_per_market_on_different_matches(db_session):
    a, b, c, d, e, f, g, h = (_team(db_session, n) for n in (
        "Arsenal", "Chelsea", "Everton", "Fulham", "Leeds", "Burnley", "Wolves", "Spurs"))
    lopsided = _match(db_session, a, b, hours=5)
    close = _match(db_session, c, d, hours=8)
    goals = _match(db_session, e, f, hours=12)
    low = _match(db_session, g, h, hours=3)
    _predict(db_session, lopsided, confidence="HIGH", prob=0.80, over15=0.70)
    # Home 0.45 / draw 0.35 -> best double chance "Everton or draw" at 0.80.
    _predict(db_session, close, confidence="MEDIUM", prob=0.45, draw=0.35, over15=0.72)
    _predict(db_session, goals, confidence="HIGH", prob=0.40, over15=0.88)
    _predict(db_session, low, confidence="LOW", prob=0.95, over15=0.99)  # never used

    picks = client.get("/api/public/free-picks").json()
    assert [(p["kind"], p["fixture"]["match_id"]) for p in picks] == [
        ("1x2", lopsided.id), ("double_chance", close.id), ("over_1_5", goals.id),
    ]
    assert [(p["market"], p["selection"], p["label"]) for p in picks] == [
        ("Match Result", "Home Win", "Arsenal to win"),
        ("Double Chance", "Home/Draw", "Everton or draw"),
        ("Total Goals 1.5", "Over 1.5", "Over 1.5 goals"),
    ]
    assert [round(p["probability"], 2) for p in picks] == [0.80, 0.80, 0.88]

    # Each pick's own fixture page shows it; other fixture pages don't.
    assert client.get(f"/api/public/fixtures/{close.id}").json()["free_pick"]["label"] == "Everton or draw"
    assert client.get(f"/api/public/fixtures/{low.id}").json()["free_pick"] is None
    # The landing call carries the same three.
    assert client.get("/api/public/landing").json()["free_picks"] == picks


def test_free_picks_prefer_soon_and_reach_further_only_when_short(db_session):
    a, b, c, d = (_team(db_session, n) for n in ("Arsenal", "Chelsea", "Everton", "Fulham"))
    soon = _match(db_session, a, b, hours=6)
    later = _match(db_session, c, d, hours=50)  # past 36h, inside the 72h fallback
    _predict(db_session, soon, confidence="HIGH", prob=0.60, over15=0.75)
    _predict(db_session, later, confidence="HIGH", prob=0.90, over15=0.95)

    picks = client.get("/api/public/free-picks").json()
    # The stronger but later match doesn't push the soon one out of the 1X2
    # slot; it fills the next market instead. Two matches -> two picks.
    assert [(p["kind"], p["fixture"]["match_id"]) for p in picks] == [("1x2", soon.id), ("double_chance", later.id)]


def test_no_eligible_fixture_means_no_free_picks(db_session):
    a, b = _team(db_session, "Arsenal"), _team(db_session, "Chelsea")
    _predict(db_session, _match(db_session, a, b, hours=5), confidence="LOW", prob=0.6, over15=0.8)
    _predict(db_session, _match(db_session, b, a, hours=100), confidence="HIGH", prob=0.8, over15=0.8)
    assert client.get("/api/public/free-picks").json() == []


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
    assert set(body) == {"branding", "stats", "track_record", "admin_pick_record", "fixtures", "free_picks", "winning_slips", "teaser_pick"}
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


def test_free_picks_stay_fixed_until_every_match_is_over(db_session):
    import datetime as dt
    a, b, c, d, e, f, g, h = (_team(db_session, n) for n in (
        "Arsenal", "Chelsea", "Everton", "Fulham", "Leeds", "Burnley", "Wolves", "Spurs"))
    m1, m2, m3 = _match(db_session, a, b, hours=2), _match(db_session, c, d, hours=4), _match(db_session, e, f, hours=6)
    for m in (m1, m2, m3):
        _predict(db_session, m, confidence="HIGH", prob=0.7, over15=0.8)
    first = client.get("/api/public/free-picks").json()
    chosen = [p["fixture"]["match_id"] for p in first]
    assert sorted(chosen) == sorted([m1.id, m2.id, m3.id])

    # A stronger fixture arriving later doesn't displace the published set,
    # nor does one of its matches kicking off and finishing.
    _predict(db_session, _match(db_session, g, h, hours=3), confidence="HIGH", prob=0.95, over15=0.99)
    m1.status, m1.home_score, m1.away_score = "FINISHED", 2, 0
    m2.status = "LIVE"
    m2.date = utcnow() - dt.timedelta(minutes=30)
    m2.home_score, m2.away_score = 0, 0
    db_session.commit()
    during = client.get("/api/public/free-picks").json()
    assert [p["fixture"]["match_id"] for p in during] == chosen
    by_match = {p["fixture"]["match_id"]: p for p in during}
    assert by_match[m2.id]["status"] == "live"
    assert by_match[m1.id]["status"] in ("won", "lost")
    assert by_match[m1.id]["probability"] == first[chosen.index(m1.id)]["probability"]

    # Once all three are over, the next read chooses a new set.
    for m in (m2, m3):
        m.status, m.home_score, m.away_score = "FINISHED", 1, 1
    db_session.commit()
    after = client.get("/api/public/free-picks").json()
    assert [p["fixture"]["match_id"] for p in after] != chosen
    assert {p["status"] for p in after} == {"upcoming"}


# --- Branding is always revalidated, so an admin's edit shows up at once -----


def test_branding_is_always_revalidated_not_cached_for_minutes(db_session):
    response = client.get("/api/public/branding")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["etag"]


def test_the_rest_of_the_public_data_is_still_cached(db_session):
    assert "max-age=300" in client.get("/api/public/stats").headers["cache-control"]


def test_an_admin_change_to_days_shows_on_the_very_next_request(db_session):
    from app import app_settings

    first = client.get("/api/public/branding")
    app_settings.set_values(
        db_session,
        {"trial_duration_days": 9, "referral_bonus_days": 4, "price_amount": 30, "price_days": 7, "price_currency": "GH₵"},
    )
    second = client.get("/api/public/branding")

    body = second.json()
    assert (body["trial_days"], body["referral_bonus_days"]) == (9, 4)
    assert body["pricing_label"] == "GH₵30 for 7 days"
    assert second.headers["etag"] != first.headers["etag"]


def test_an_unchanged_branding_answers_with_a_tiny_304(db_session):
    first = client.get("/api/public/branding")

    again = client.get("/api/public/branding", headers={"If-None-Match": first.headers["etag"]})

    assert again.status_code == 304 and again.content == b""
    assert again.headers["etag"] == first.headers["etag"]


def test_a_stale_etag_gets_the_full_fresh_answer(db_session):
    from app import app_settings

    first = client.get("/api/public/branding")
    app_settings.set_values(db_session, {"trial_duration_days": 11})

    after = client.get("/api/public/branding", headers={"If-None-Match": first.headers["etag"]})

    assert after.status_code == 200 and after.json()["trial_days"] == 11


def test_the_landing_bundle_still_carries_the_same_branding(db_session):
    assert client.get("/api/public/landing").json()["branding"] == client.get("/api/public/branding").json()
