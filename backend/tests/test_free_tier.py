"""The free/premium split: a logged-in account with no redeemed code gets a
small, genuine taste of the model (one headline pick per league) and nothing
else -- the prediction-serving surface itself stays behind require_active_access.
"""

from __future__ import annotations

import datetime as dt

from fastapi.testclient import TestClient

from app.db.models import Match, Team
from app.prediction_models.elo import rebuild_elo_history


def _seed_two_leagues(db_session) -> None:
    base_date = dt.datetime.utcnow() - dt.timedelta(days=120)
    fixtures = [(0, 1), (2, 3), (1, 2), (0, 3), (1, 3), (0, 2), (3, 1), (2, 0)]
    fixtures += [(1, 0), (3, 2), (2, 1), (3, 0), (3, 1), (2, 0), (1, 3), (0, 2)]

    for league in ("League One", "League Two"):
        teams = [Team(name=f"{league} Club {i}", league=league) for i in range(4)]
        db_session.add_all(teams)
        db_session.flush()

        for week, (home, away) in enumerate(fixtures):
            db_session.add(
                Match(
                    league=league,
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
        rebuild_elo_history(db_session, league)

        db_session.add(
            Match(
                league=league,
                season="2324",
                date=dt.datetime.utcnow() + dt.timedelta(hours=12),
                home_team_id=teams[0].id,
                away_team_id=teams[1].id,
                status="SCHEDULED",
            )
        )
    db_session.commit()


def test_free_picks_work_without_an_access_grant(db_session, headers_no_access):
    from app.main import app

    _seed_two_leagues(db_session)
    client = TestClient(app)

    # Build predictions first (as an authorized caller would), since
    # free-picks reads only and never generates on demand.
    response = client.get("/api/predictions/free-picks", headers=headers_no_access)
    assert response.status_code == 200
    # No predictions exist yet, so the read-only endpoint finds nothing --
    # that's correct, not a failure.
    assert response.json() == []


def test_free_picks_reads_existing_predictions_one_per_league(db_session, auth_headers, headers_no_access):
    from app.main import app

    _seed_two_leagues(db_session)
    client = TestClient(app)

    matches = db_session.query(Match).filter(Match.status == "SCHEDULED").all()
    for m in matches:
        built = client.get(f"/api/matches/{m.id}/prediction", headers=auth_headers)
        assert built.status_code == 200, built.text

    response = client.get("/api/predictions/free-picks", headers=headers_no_access)
    assert response.status_code == 200
    picks = response.json()

    assert {p["league"] for p in picks} == {"League One", "League Two"}
    assert len(picks) == 2  # one per league
    for pick in picks:
        assert abs(pick["home_win"] + pick["draw"] + pick["away_win"] - 1.0) < 1e-6
        assert pick["probability"] > 0
        assert pick["selection"]
        # The free tier's whole point: no full market breakdown here.
        assert "over_probabilities" not in pick
        assert "correct_score_probabilities" not in pick
        assert "explanation" not in pick


def test_free_tier_does_not_unlock_the_full_prediction_surface(db_session, headers_no_access):
    from app.main import app

    _seed_two_leagues(db_session)
    client = TestClient(app)

    for path in ("/api/predictions/today", "/api/predictions/high-confidence", "/api/predictions/most-likely", "/api/predictions/outcomes"):
        response = client.get(path, headers=headers_no_access)
        assert response.status_code == 403, f"{path} should still require an active access grant"


def test_login_is_still_required_for_free_picks():
    from app.main import app

    client = TestClient(app)
    response = client.get("/api/predictions/free-picks")
    assert response.status_code == 401
