import datetime as dt

from fastapi.testclient import TestClient

from app.db.models import Match, Team
from app.prediction_models.elo import rebuild_elo_history


def _seed_league(db_session) -> tuple[Team, Team]:
    teams = [Team(name=f"Club {i}", league="Test League") for i in range(4)]
    db_session.add_all(teams)
    db_session.flush()

    base_date = dt.datetime(2024, 1, 1)
    # A double round-robin of finished matches so every team has >= 5 games
    # of history (the outcome registry's minimum data requirement).
    fixtures = [(0, 1), (2, 3), (1, 2), (0, 3), (1, 3), (0, 2), (3, 1), (2, 0)]
    fixtures += [(1, 0), (3, 2), (2, 1), (3, 0), (3, 1), (2, 0), (1, 3), (0, 2)]
    for week, (home, away) in enumerate(fixtures):
        db_session.add(
            Match(
                league="Test League",
                season="2324",
                date=base_date + dt.timedelta(days=7 * week),
                home_team_id=teams[home].id,
                away_team_id=teams[away].id,
                home_score=2,
                away_score=1,
                status="FINISHED",
            )
        )
    db_session.commit()
    rebuild_elo_history(db_session, "Test League")

    upcoming = Match(
        league="Test League",
        season="2324",
        date=base_date + dt.timedelta(days=100),
        home_team_id=teams[0].id,
        away_team_id=teams[1].id,
        status="SCHEDULED",
    )
    db_session.add(upcoming)
    db_session.commit()
    db_session.refresh(upcoming)
    return teams, upcoming


def test_health_endpoint():
    """Liveness is separate from database reachability, and the endpoint
    reports both -- see tests/test_startup_resilience.py for the case where
    they differ."""

    from app.main import app

    client = TestClient(app)
    response = client.get("/api/health")
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ready"


def test_protected_endpoint_requires_auth(db_session):
    from app.main import app

    _, upcoming = _seed_league(db_session)

    client = TestClient(app)
    response = client.get(f"/api/matches/{upcoming.id}/prediction")
    assert response.status_code == 401


def test_match_prediction_endpoint(db_session, auth_headers):
    from app.main import app

    _, upcoming = _seed_league(db_session)

    client = TestClient(app)
    response = client.get(f"/api/matches/{upcoming.id}/prediction", headers=auth_headers)
    assert response.status_code == 200

    body = response.json()
    assert abs(body["home_win"] + body["draw"] + body["away_win"] - 1.0) < 1e-6
    assert body["confidence"] in {"HIGH", "MEDIUM", "LOW"}
    assert body["global_outcome"]["probability"] > 0
    assert "positive" in body["explanation"]
    assert "negative" in body["explanation"]


def test_match_outcomes_endpoint_returns_every_market_for_that_match(db_session, auth_headers):
    """The per-match complement to /api/predictions/outcomes: every market
    the registry offers for one fixture, not the top few or a slice of one
    market compared across a whole league."""

    from app.main import app

    _, upcoming = _seed_league(db_session)

    client = TestClient(app)
    # Build the prediction first so its model_breakdown (and therefore the
    # matrix-derived markets) definitely exists before outcomes are read.
    client.get(f"/api/matches/{upcoming.id}/prediction", headers=auth_headers)

    response = client.get(f"/api/matches/{upcoming.id}/outcomes", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()

    assert body["total_matches"] == 1
    assert len(body["leagues"]) == 1
    league = body["leagues"][0]
    assert league["league"] == "Test League"
    assert league["matches"] == 1
    assert all(o["match_id"] == upcoming.id for o in league["outcomes"])

    market_names = {m["market"] for m in body["markets"]}
    # The base registry...
    assert {"Match Result", "Both Teams To Score"} <= market_names
    # ...and the matrix-derived expansion, which only appears once a
    # prediction with lambda_home/lambda_away has actually been generated.
    assert {"Winning Margin", "Home Clean Sheet", "Total Goals Range"} <= market_names

    outcomes_1x2 = [o for o in league["outcomes"] if o["market"] == "Match Result"]
    assert abs(sum(o["probability"] for o in outcomes_1x2) - 1.0) < 1e-6


def test_list_matches_endpoint(db_session, auth_headers):
    from app.main import app

    _seed_league(db_session)

    client = TestClient(app)
    response = client.get("/api/matches", params={"league": "Test League"}, headers=auth_headers)
    assert response.status_code == 200
    assert len(response.json()) == 17  # 16 finished + 1 scheduled


def test_list_matches_filters_by_team(db_session, auth_headers):
    from app.main import app

    teams, _ = _seed_league(db_session)

    client = TestClient(app)
    response = client.get("/api/matches", params={"league": "Test League", "team_id": teams[0].id}, headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert len(body) > 0
    assert all(m["home_team"]["id"] == teams[0].id or m["away_team"]["id"] == teams[0].id for m in body)


def test_head_to_head_endpoint(db_session, auth_headers):
    from app.main import app

    teams, _ = _seed_league(db_session)

    client = TestClient(app)
    response = client.get(f"/api/teams/{teams[0].id}/head-to-head/{teams[1].id}", headers=auth_headers)
    assert response.status_code == 200
    meetings = response.json()
    assert len(meetings) > 0
    for m in meetings:
        assert {m["home_team"], m["away_team"]} == {"Club 0", "Club 1"}


def test_prediction_history_endpoint(db_session, auth_headers):
    from app.main import app

    _, upcoming = _seed_league(db_session)

    client = TestClient(app)
    first = client.get(f"/api/matches/{upcoming.id}/prediction", headers=auth_headers)
    assert first.status_code == 200

    history = client.get(f"/api/matches/{upcoming.id}/prediction-history", headers=auth_headers)
    assert history.status_code == 200
    body = history.json()
    assert len(body) == 1
    assert body[0]["match_id"] == upcoming.id


def test_root_is_a_signpost_not_a_404(db_session):
    """Opening the service's base URL is the first thing anyone does after a
    deploy. FastAPI's bare {"detail":"Not Found"} there reads as a broken
    deployment when the API is running fine, so / answers with what the
    service is and where to go next."""

    from app.main import app

    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "ok"
    assert body["docs"] == "/docs"
    assert body["health"] == "/api/health"
    assert "/api/public/stats" in body["public_data"]


def test_root_needs_no_authentication(db_session):
    """It must work before anyone has an account -- that's the situation it
    exists for."""

    from app.main import app

    client = TestClient(app)
    assert "Authorization" not in client.headers
    assert client.get("/").status_code == 200
