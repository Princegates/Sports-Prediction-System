"""End-to-end wiring for squad-absence data into a real built Prediction --
the unit tests in test_squad_strength.py and test_explain.py cover the logic
in isolation, this confirms build_prediction_for_match actually calls
through to it and the result lands on the row members look at.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app import prediction_service
from app.db.models import Match, MatchLineup, Player, PlayerAbsence, Team
from app.prediction_models import elo
from app.prediction_models.ml_model import MLPrediction
from app.prediction_service import build_prediction_for_match

LEAGUE = "Prediction Service Test League"
BASE = dt.datetime(2026, 1, 1, 15, 0)


class _StubMLModel:
    def predict(self, feature_row: dict[str, float]) -> MLPrediction:
        return MLPrediction(home_win=0.45, draw=0.3, away_win=0.25, over_2_5=0.5, btts_yes=0.5)


@pytest.fixture(autouse=True)
def _trained_model(monkeypatch):
    monkeypatch.setattr(prediction_service, "load_ml_model", lambda league: _StubMLModel())


@pytest.fixture()
def seeded_league(db_session):
    teams = []
    for name in ("Alpha", "Beta", "Gamma", "Delta"):
        t = Team(name=f"{name} FC", league=LEAGUE, aliases=[])
        db_session.add(t)
        teams.append(t)
    db_session.commit()
    for t in teams:
        db_session.refresh(t)

    day = 0
    for home in teams:
        for away in teams:
            if home.id == away.id:
                continue
            db_session.add(
                Match(
                    league=LEAGUE, season="2025-26", date=BASE + dt.timedelta(days=day),
                    home_team_id=home.id, away_team_id=away.id,
                    home_score=(day % 3), away_score=(day % 2), status="FINISHED",
                )
            )
            day += 1
    db_session.commit()
    elo.rebuild_elo_history(db_session, LEAGUE)

    upcoming = Match(
        league=LEAGUE, season="2025-26", date=BASE + dt.timedelta(days=400),
        home_team_id=teams[0].id, away_team_id=teams[1].id, status="SCHEDULED",
    )
    db_session.add(upcoming)
    db_session.commit()
    db_session.refresh(upcoming)
    return teams, upcoming


def test_a_match_with_no_squad_data_builds_cleanly(db_session, seeded_league):
    """The common case for a long while -- a fresh deployment or a league
    squad_ingest hasn't been run for yet -- must produce an ordinary
    prediction, not an error."""

    _, match = seeded_league
    prediction = build_prediction_for_match(db_session, match)
    assert prediction.home_win + prediction.draw + prediction.away_win == pytest.approx(1.0)
    assert not any("missing" in line for line in prediction.explanation["positive"] + prediction.explanation["negative"])


def test_a_significant_home_absence_shifts_probability_toward_away(db_session, seeded_league):
    teams, match = seeded_league
    home = teams[0]

    without = build_prediction_for_match(db_session, match)

    player = Player(api_player_id=1, name="Home Star", team_id=home.id)
    db_session.add(player)
    db_session.commit()
    db_session.refresh(player)
    for i in range(5):
        past = Match(
            league=LEAGUE, season="2024-25", date=BASE - dt.timedelta(days=i + 1),
            home_team_id=home.id, away_team_id=teams[2].id, status="FINISHED", home_score=1, away_score=0,
        )
        db_session.add(past)
        db_session.commit()
        db_session.refresh(past)
        db_session.add(MatchLineup(match_id=past.id, team_id=home.id, player_id=player.id, is_starter=True))
    db_session.add(PlayerAbsence(player_id=player.id, team_id=home.id, match_id=match.id, reason="Injury"))
    db_session.commit()

    with_absence = build_prediction_for_match(db_session, match)

    assert with_absence.home_win < without.home_win
    assert with_absence.away_win > without.away_win
    assert with_absence.home_win + with_absence.draw + with_absence.away_win == pytest.approx(1.0)
    assert any(f"{home.name} missing Home Star" in line for line in with_absence.explanation["negative"])


def test_the_probability_shift_never_exceeds_the_documented_cap(db_session, seeded_league):
    """Even an absurd number of reported absences can't swing the match by
    more than squad_strength.MAX_PROBABILITY_SHIFT -- there's no historical
    calibration behind this signal yet, so it must stay conservative by
    construction, not by how reasonable the input happens to be."""

    from app.features.squad_strength import MAX_PROBABILITY_SHIFT

    teams, match = seeded_league
    home = teams[0]

    without = build_prediction_for_match(db_session, match)

    for i in range(10):
        player = Player(api_player_id=100 + i, name=f"Player {i}", team_id=home.id)
        db_session.add(player)
        db_session.commit()
        db_session.refresh(player)
        past = Match(
            league=LEAGUE, season="2024-25", date=BASE - dt.timedelta(days=i + 1),
            home_team_id=home.id, away_team_id=teams[2].id, status="FINISHED", home_score=1, away_score=0,
        )
        db_session.add(past)
        db_session.commit()
        db_session.refresh(past)
        db_session.add(MatchLineup(match_id=past.id, team_id=home.id, player_id=player.id, is_starter=True))
        db_session.add(PlayerAbsence(player_id=player.id, team_id=home.id, match_id=match.id, reason="Injury"))
    db_session.commit()

    with_absences = build_prediction_for_match(db_session, match)
    assert without.home_win - with_absences.home_win <= MAX_PROBABILITY_SHIFT + 1e-6
