import datetime as dt

from app.db.models import Match, Team
from app.prediction_models import elo


def test_expected_score_symmetry():
    assert elo.expected_score(1500, 1500) == 0.5
    assert elo.expected_score(1600, 1500) > 0.5
    assert abs(elo.expected_score(1600, 1500) + elo.expected_score(1500, 1600) - 1.0) < 1e-9


def test_elo_diff_to_1x2_sums_to_one_and_is_monotonic():
    even = elo.elo_diff_to_1x2(0)
    assert abs(even.home_win - even.away_win) < 1e-9
    assert abs(even.home_win + even.draw + even.away_win - 1.0) < 1e-9

    favored_home = elo.elo_diff_to_1x2(200)
    assert favored_home.home_win > even.home_win
    assert favored_home.away_win < even.away_win
    assert abs(favored_home.home_win + favored_home.draw + favored_home.away_win - 1.0) < 1e-9


def test_rebuild_elo_history_rewards_winner(db_session):
    home = Team(name="Home FC", league="Test League")
    away = Team(name="Away FC", league="Test League")
    db_session.add_all([home, away])
    db_session.flush()

    match = Match(
        league="Test League",
        season="2425",
        date=dt.datetime(2024, 8, 1),
        home_team_id=home.id,
        away_team_id=away.id,
        home_score=3,
        away_score=0,
        status="FINISHED",
    )
    db_session.add(match)
    db_session.commit()

    ratings = elo.rebuild_elo_history(db_session, "Test League")
    assert ratings[home.id] > 1500
    assert ratings[away.id] < 1500

    rating_after_for_away = elo.get_rating_before(db_session, away.id, dt.datetime(2024, 8, 2))
    assert rating_after_for_away == ratings[away.id]
