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
    from app.main import app

    client = TestClient(app)
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_match_prediction_endpoint(db_session):
    from app.main import app

    _, upcoming = _seed_league(db_session)

    client = TestClient(app)
    response = client.get(f"/api/matches/{upcoming.id}/prediction")
    assert response.status_code == 200

    body = response.json()
    assert abs(body["home_win"] + body["draw"] + body["away_win"] - 1.0) < 1e-6
    assert body["confidence"] in {"HIGH", "MEDIUM", "LOW"}
    assert body["global_outcome"]["probability"] > 0
    assert "positive" in body["explanation"]
    assert "negative" in body["explanation"]


def test_list_matches_endpoint(db_session):
    from app.main import app

    _seed_league(db_session)

    client = TestClient(app)
    response = client.get("/api/matches", params={"league": "Test League"})
    assert response.status_code == 200
    assert len(response.json()) == 17  # 16 finished + 1 scheduled


def test_list_matches_filters_by_team(db_session):
    from app.main import app

    teams, _ = _seed_league(db_session)

    client = TestClient(app)
    response = client.get("/api/matches", params={"league": "Test League", "team_id": teams[0].id})
    assert response.status_code == 200
    body = response.json()
    assert len(body) > 0
    assert all(m["home_team"]["id"] == teams[0].id or m["away_team"]["id"] == teams[0].id for m in body)


def test_head_to_head_endpoint(db_session):
    from app.main import app

    teams, _ = _seed_league(db_session)

    client = TestClient(app)
    response = client.get(f"/api/teams/{teams[0].id}/head-to-head/{teams[1].id}")
    assert response.status_code == 200
    meetings = response.json()
    assert len(meetings) > 0
    for m in meetings:
        assert {m["home_team"], m["away_team"]} == {"Club 0", "Club 1"}


def test_prediction_history_endpoint(db_session):
    from app.main import app

    _, upcoming = _seed_league(db_session)

    client = TestClient(app)
    first = client.get(f"/api/matches/{upcoming.id}/prediction")
    assert first.status_code == 200

    history = client.get(f"/api/matches/{upcoming.id}/prediction-history")
    assert history.status_code == 200
    body = history.json()
    assert len(body) == 1
    assert body[0]["match_id"] == upcoming.id
