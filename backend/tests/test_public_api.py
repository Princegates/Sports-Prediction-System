"""Tests for the unauthenticated landing-page endpoints.

Two things matter here. First, these routes must work with no credentials --
they feed the welcome page, which anonymous visitors see. Second, and more
important, they must not leak the product: a visitor may learn that the system
covers 380 matches and scored 54% on a held-out split, but not which selection
the model likes tonight.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app.db.models import Match, ModelMetric, Prediction, Team, User
from app.main import app

client = TestClient(app)

LEAGUE = "English Premier League"


@pytest.fixture()
def seeded(db_session):
    home = Team(name="Arsenal", league=LEAGUE, country="England", aliases=[])
    away = Team(name="Chelsea", league=LEAGUE, country="England", aliases=[])
    db_session.add_all([home, away])
    db_session.commit()
    db_session.refresh(home)
    db_session.refresh(away)

    now = dt.datetime.utcnow()
    played = Match(
        league=LEAGUE, season="2025-26", date=now - dt.timedelta(days=10),
        home_team_id=home.id, away_team_id=away.id,
        home_score=2, away_score=1, status="FINISHED",
    )
    upcoming = Match(
        league=LEAGUE, season="2025-26", date=now + dt.timedelta(hours=8),
        home_team_id=home.id, away_team_id=away.id, status="SCHEDULED",
    )
    db_session.add_all([played, upcoming])
    db_session.commit()
    db_session.refresh(upcoming)

    db_session.add(
        Prediction(
            match_id=upcoming.id, model_version="ensemble-v1",
            home_win=0.52, draw=0.26, away_win=0.22,
            over_probabilities={"2.5": 0.57}, btts_yes=0.61, btts_no=0.39,
            correct_score_probabilities={"2-1": 0.11},
            most_likely_score="2-1", most_likely_score_probability=0.11,
            global_outcome_market="Match Result", global_outcome_selection="Home Win",
            global_outcome_probability=0.52,
            confidence="HIGH", data_quality_score=0.9, model_agreement_score=0.88,
            explanation={"positive": ["secret reasoning"], "negative": []},
            model_breakdown={},
        )
    )
    db_session.commit()
    return {"upcoming": upcoming}


def test_stats_needs_no_authentication(db_session, seeded):
    response = client.get("/api/public/stats")
    assert response.status_code == 200
    body = response.json()
    assert body["matches_analyzed"] == 1  # only the finished match counts
    assert body["teams_tracked"] == 2
    assert body["leagues_covered"] == 1
    assert body["league_names"] == [LEAGUE]


def test_stats_counts_only_active_members(db_session, seeded):
    from app.auth.passwords import hash_password

    db_session.add_all(
        [
            User(email="a@x.com", name="A", password_hash=hash_password("x" * 10), status="active"),
            User(email="b@x.com", name="B", password_hash=hash_password("x" * 10), status="suspended"),
        ]
    )
    db_session.commit()

    assert client.get("/api/public/stats").json()["active_members"] == 1


def test_accuracy_reports_no_data_rather_than_a_placeholder(db_session, seeded):
    """With no backtest recorded, the landing page must be told there is no
    figure -- not handed an invented one to display."""

    body = client.get("/api/public/accuracy").json()
    assert body["has_data"] is False
    assert body["accuracy"] is None


def test_accuracy_prefers_the_held_out_test_split(db_session, seeded):
    db_session.add_all(
        [
            ModelMetric(model_version="ensemble-v1", split="train", league=LEAGUE, metric_name="accuracy", metric_value=0.83),
            ModelMetric(model_version="ensemble-v1", split="test", league=LEAGUE, metric_name="accuracy", metric_value=0.55),
            ModelMetric(model_version="ensemble-v1", split="test", league=LEAGUE, metric_name="n", metric_value=380),
        ]
    )
    db_session.commit()

    body = client.get("/api/public/accuracy").json()
    assert body["has_data"] is True
    assert body["split"] == "test"
    assert body["accuracy"] == 0.55
    assert body["matches_evaluated"] == 380


def test_public_fixtures_expose_no_selection_or_probability(db_session, seeded):
    """This is the endpoint most at risk of giving the product away. It may
    say a prediction exists and how confident it is; it must not say what the
    prediction is."""

    body = client.get("/api/public/fixtures").json()
    assert len(body) == 1
    fixture = body[0]

    assert fixture["home_team"] == "Arsenal"
    assert fixture["has_prediction"] is True
    assert fixture["confidence"] == "HIGH"

    leaked = {"global_outcome_selection", "global_outcome_probability", "home_win", "explanation"}
    assert leaked.isdisjoint(fixture.keys())
    assert "Home Win" not in str(fixture)
    assert "0.52" not in str(fixture)


def test_public_fixtures_excludes_matches_already_played(db_session, seeded):
    fixtures = client.get("/api/public/fixtures").json()
    assert all(f["home_team"] == "Arsenal" for f in fixtures)
    assert len(fixtures) == 1  # the finished match is not upcoming


def test_public_fixtures_clamps_absurd_parameters(db_session, seeded):
    assert client.get("/api/public/fixtures?days_ahead=9999&limit=9999").status_code == 200
    assert client.get("/api/public/fixtures?days_ahead=-5&limit=0").status_code == 200


def test_predictions_still_require_authentication(db_session, seeded):
    """Sanity check that adding public routes didn't open the real ones."""

    assert client.get("/api/predictions/today").status_code == 401
    assert client.get(f"/api/matches/{seeded['upcoming'].id}/prediction").status_code == 401


def _seed_metrics(db, rows):
    """rows: (league, metric_name, value) for the 'test' split."""
    db.add_all([
        ModelMetric(model_version="ensemble-v1", split="test", league=lg, metric_name=name, metric_value=val)
        for lg, name, val in rows
    ])
    db.commit()


def test_multi_league_accuracy_is_combined_not_overwritten(db_session, seeded):
    """The bug this pins: every league writes a ('test', 'accuracy') row, so
    keying metrics by split alone let the last league silently overwrite the
    rest -- reporting one league's figure while naming five beside it.

    Weighted by evaluated matches, 45.9% over 290 and 52.9% over 289 combine
    to ~49.4%. Neither input value may be returned as the headline.
    """

    _seed_metrics(db_session, [
        ("English Premier League", "accuracy", 0.459), ("English Premier League", "n", 290),
        ("Italian Serie A", "accuracy", 0.529), ("Italian Serie A", "n", 289),
    ])

    body = client.get("/api/public/accuracy").json()

    assert body["has_data"] is True
    assert 0.459 < body["accuracy"] < 0.529, "headline is one league's figure, not a combination"
    assert body["accuracy"] == pytest.approx(0.4939, abs=1e-3)
    assert body["matches_evaluated"] == 579, "evaluated matches must sum across leagues"
    assert len(body["leagues"]) == 2


def test_accuracy_is_weighted_by_matches_not_a_plain_mean(db_session, seeded):
    """A league evaluated on 10 matches must not swing the headline as hard
    as one evaluated on 500."""

    _seed_metrics(db_session, [
        ("Big League", "accuracy", 0.50), ("Big League", "n", 500),
        ("Tiny League", "accuracy", 0.90), ("Tiny League", "n", 10),
    ])

    accuracy = client.get("/api/public/accuracy").json()["accuracy"]

    plain_mean = 0.70
    weighted = (0.50 * 500 + 0.90 * 10) / 510
    assert accuracy == pytest.approx(weighted, abs=1e-4)
    assert abs(accuracy - plain_mean) > 0.15, "looks like an unweighted average"


def test_per_league_breakdown_is_exposed(db_session, seeded):
    """The spread between leagues is real and worth showing -- an overall
    number alone hides that some leagues are much harder."""

    _seed_metrics(db_session, [
        ("English Premier League", "accuracy", 0.459), ("English Premier League", "n", 290),
        ("Italian Serie A", "accuracy", 0.529), ("Italian Serie A", "n", 289),
    ])

    per_league = client.get("/api/public/accuracy").json()["per_league"]

    assert [r["league"] for r in per_league] == ["Italian Serie A", "English Premier League"], "best first"
    assert per_league[0]["accuracy"] == pytest.approx(0.529)
    assert per_league[0]["matches_evaluated"] == 289


def test_single_league_accuracy_is_unchanged(db_session, seeded):
    """The aggregation must not disturb the one-league case."""

    _seed_metrics(db_session, [
        ("English Premier League", "accuracy", 0.459), ("English Premier League", "n", 290),
    ])

    body = client.get("/api/public/accuracy").json()
    assert body["accuracy"] == pytest.approx(0.459)
    assert body["matches_evaluated"] == 290


def test_track_record_reports_no_data_before_any_match_finishes(db_session):
    """A fresh deployment (no graded predictions yet) gets an explicit
    'no data' rather than a fabricated hit rate."""

    body = client.get("/api/public/track-record").json()
    assert body == {"has_data": False, "graded_predictions": 0, "hit_rate": None, "by_confidence": [], "since": None}


def test_track_record_grades_the_first_prediction_per_match(db_session, seeded):
    """A correct pre-match call on a since-finished match counts as a hit;
    only the *first* prediction stored for that match is graded, even if it
    was later regenerated."""

    played = db_session.query(Match).filter(Match.status == "FINISHED").one()  # home 2 - away 1 -> "H"

    def make_prediction(home_win, created_at):
        return Prediction(
            match_id=played.id, model_version="ensemble-v1", created_at=created_at,
            home_win=home_win, draw=0.25, away_win=1 - home_win - 0.25,
            over_probabilities={"2.5": 0.5}, btts_yes=0.5, btts_no=0.5,
            correct_score_probabilities={"2-1": 0.1}, most_likely_score="2-1", most_likely_score_probability=0.1,
            global_outcome_market="Match Result", global_outcome_selection="Home Win", global_outcome_probability=home_win,
            confidence="HIGH", data_quality_score=0.9, model_agreement_score=0.88,
            explanation={"positive": [], "negative": []}, model_breakdown={},
        )

    now = dt.datetime.utcnow()
    # First (earliest) prediction correctly picks the home win that happened.
    db_session.add(make_prediction(0.60, now - dt.timedelta(days=9)))
    # A later regeneration wrongly favors away -- must NOT be the one graded.
    db_session.add(make_prediction(0.10, now - dt.timedelta(days=1)))
    db_session.commit()

    body = client.get("/api/public/track-record").json()
    assert body["has_data"] is True
    assert body["graded_predictions"] == 1
    assert body["hit_rate"] == pytest.approx(1.0)
    assert body["by_confidence"] == [{"confidence": "HIGH", "graded": 1, "hit_rate": 1.0}]


def test_track_record_never_grades_a_prediction_for_an_unplayed_match(db_session, seeded):
    """The seeded prediction belongs to the SCHEDULED (not yet played)
    fixture -- it must not be graded as a hit or a miss."""

    body = client.get("/api/public/track-record").json()
    assert body["has_data"] is False


def test_accuracy_never_reports_a_baseline_as_the_models_own(db_session, seeded):
    """scripts/backtest.py stores baseline-* rows alongside the real model's
    with the SAME computed_at timestamp. A tie-break on computed_at alone
    could land on a baseline and quote its accuracy as if it were the
    model's -- this pins that it never does, even when the baseline row is
    inserted after the real one."""

    now = dt.datetime.utcnow()
    db_session.add_all([
        ModelMetric(model_version="ensemble-v1", split="test", league="English Premier League", metric_name="accuracy", metric_value=0.459, computed_at=now),
        ModelMetric(model_version="ensemble-v1", split="test", league="English Premier League", metric_name="n", metric_value=290, computed_at=now),
        # Inserted after, same timestamp -- must not become "the newest".
        ModelMetric(model_version="baseline-majority_class", split="test", league="English Premier League", metric_name="accuracy", metric_value=0.386, computed_at=now),
    ])
    db_session.commit()

    body = client.get("/api/public/accuracy").json()
    assert body["model_version"] == "ensemble-v1"
    assert body["accuracy"] == pytest.approx(0.459)
