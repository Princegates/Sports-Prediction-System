"""Squad availability: start-rate, absence impact, and the bounded
probability nudge built from them.

Every function here must degrade to "nothing" on a fresh deployment or a
team with no accumulated lineup history -- there was nothing to backfill
this from, so the common case for a long while is zero data, not malformed
data, and that must never look like a crash or a wild prediction swing.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.db.models import Match, MatchLineup, Player, PlayerAbsence, Team
from app.features.squad_strength import (
    MAX_PROBABILITY_SHIFT,
    AbsenceImpact,
    absence_probability_nudge,
    player_start_rate,
    team_absence_impact,
)

LEAGUE = "Squad Test League"
BASE = dt.datetime(2026, 1, 1, 15, 0)


@pytest.fixture()
def clubs(db_session):
    home = Team(name="Home FC", league=LEAGUE, aliases=[])
    away = Team(name="Away FC", league=LEAGUE, aliases=[])
    db_session.add_all([home, away])
    db_session.commit()
    db_session.refresh(home)
    db_session.refresh(away)
    return home, away


def _make_match(db_session, home, away, *, days_ago: int, status: str = "FINISHED") -> Match:
    match = Match(
        league=LEAGUE,
        season="2025-26",
        date=BASE - dt.timedelta(days=days_ago),
        home_team_id=home.id,
        away_team_id=away.id,
        status=status,
        home_score=1 if status == "FINISHED" else None,
        away_score=0 if status == "FINISHED" else None,
    )
    db_session.add(match)
    db_session.commit()
    db_session.refresh(match)
    return match


def _make_player(db_session, api_id: int, name: str, team: Team) -> Player:
    player = Player(api_player_id=api_id, name=name, team_id=team.id)
    db_session.add(player)
    db_session.commit()
    db_session.refresh(player)
    return player


class TestPlayerStartRate:
    def test_zero_with_no_lineup_history(self, db_session, clubs):
        home, _ = clubs
        player = _make_player(db_session, 1, "No History", home)
        assert player_start_rate(db_session, player.id, BASE) == 0.0

    def test_reflects_recent_appearances(self, db_session, clubs):
        home, away = clubs
        player = _make_player(db_session, 1, "Regular Starter", home)
        # Started 3 of the last 5 appearances.
        pattern = [True, True, False, True, False]
        for i, started in enumerate(pattern):
            match = _make_match(db_session, home, away, days_ago=10 - i)
            db_session.add(MatchLineup(match_id=match.id, team_id=home.id, player_id=player.id, is_starter=started))
        db_session.commit()

        assert player_start_rate(db_session, player.id, BASE) == pytest.approx(0.6)

    def test_window_caps_how_far_back_it_looks(self, db_session, clubs):
        home, away = clubs
        player = _make_player(db_session, 1, "Long History", home)
        # 2 starts out of the most recent 2 (window=2), ignoring 8 older benchings.
        for i in range(8):
            match = _make_match(db_session, home, away, days_ago=20 + i)
            db_session.add(MatchLineup(match_id=match.id, team_id=home.id, player_id=player.id, is_starter=False))
        for i in range(2):
            match = _make_match(db_session, home, away, days_ago=i + 1)
            db_session.add(MatchLineup(match_id=match.id, team_id=home.id, player_id=player.id, is_starter=True))
        db_session.commit()

        assert player_start_rate(db_session, player.id, BASE, window=2) == pytest.approx(1.0)

    def test_ignores_appearances_on_or_after_as_of(self, db_session, clubs):
        """Leakage guard: a prediction for a match on day X must never see a
        lineup from day X or later."""

        home, away = clubs
        player = _make_player(db_session, 1, "Future Starter", home)
        future_match = _make_match(db_session, home, away, days_ago=-5)  # in the future relative to BASE
        db_session.add(MatchLineup(match_id=future_match.id, team_id=home.id, player_id=player.id, is_starter=True))
        db_session.commit()

        assert player_start_rate(db_session, player.id, BASE) == 0.0


class TestTeamAbsenceImpact:
    def test_empty_with_no_data(self, db_session, clubs):
        home, _ = clubs
        impact = team_absence_impact(db_session, home.id, BASE)
        assert impact.missing_players == []
        assert impact.strength_lost == 0.0

    def test_reported_injury_contributes_the_players_own_start_rate(self, db_session, clubs):
        home, away = clubs
        player = _make_player(db_session, 1, "Star Striker", home)
        for i in range(5):
            match = _make_match(db_session, home, away, days_ago=i + 1)
            db_session.add(MatchLineup(match_id=match.id, team_id=home.id, player_id=player.id, is_starter=True))
        db_session.add(PlayerAbsence(player_id=player.id, team_id=home.id, reason="Suspended"))
        db_session.commit()

        impact = team_absence_impact(db_session, home.id, BASE)
        assert impact.missing_players == ["Star Striker"]
        assert impact.strength_lost == pytest.approx(1.0)

    def test_a_fringe_players_injury_barely_moves_the_impact(self, db_session, clubs):
        home, away = clubs
        player = _make_player(db_session, 1, "Fringe Player", home)
        for i, started in enumerate([False, False, False, False, True]):
            match = _make_match(db_session, home, away, days_ago=i + 1)
            db_session.add(MatchLineup(match_id=match.id, team_id=home.id, player_id=player.id, is_starter=started))
        db_session.add(PlayerAbsence(player_id=player.id, team_id=home.id, reason="Knock"))
        db_session.commit()

        impact = team_absence_impact(db_session, home.id, BASE)
        assert impact.strength_lost == pytest.approx(0.2)

    def test_an_absence_with_no_lineup_history_at_all_is_not_counted(self, db_session, clubs):
        """A reported absence for a player this project has never seen in a
        lineup contributes nothing -- there's no basis to call them
        important, and start_rate correctly returns 0 for them."""

        home, _ = clubs
        player = _make_player(db_session, 1, "Unknown Quantity", home)
        db_session.add(PlayerAbsence(player_id=player.id, team_id=home.id, reason="Injury"))
        db_session.commit()

        impact = team_absence_impact(db_session, home.id, BASE)
        assert impact.missing_players == []

    def test_absence_tied_to_a_different_match_does_not_count(self, db_session, clubs):
        home, away = clubs
        player = _make_player(db_session, 1, "Star Striker", home)
        for i in range(5):
            match = _make_match(db_session, home, away, days_ago=i + 1)
            db_session.add(MatchLineup(match_id=match.id, team_id=home.id, player_id=player.id, is_starter=True))
        other_match = _make_match(db_session, home, away, days_ago=30)
        db_session.add(PlayerAbsence(player_id=player.id, team_id=home.id, match_id=other_match.id, reason="Suspended"))
        db_session.commit()

        this_match = _make_match(db_session, home, away, days_ago=0, status="SCHEDULED")
        impact = team_absence_impact(db_session, home.id, BASE, match=this_match)
        assert impact.missing_players == []

    def test_confirmed_lineup_surprise_is_detected(self, db_session, clubs):
        """A regular starter, never reported injured, simply isn't in
        today's confirmed XI -- the tactical-rest / late-fitness-call case
        the daily batch has no way to have known about."""

        home, away = clubs
        regular = _make_player(db_session, 1, "Regular Starter", home)
        for i in range(8):
            match = _make_match(db_session, home, away, days_ago=i + 10)
            db_session.add(MatchLineup(match_id=match.id, team_id=home.id, player_id=regular.id, is_starter=True))
        db_session.commit()

        today = _make_match(db_session, home, away, days_ago=0, status="SCHEDULED")
        # A confirmed lineup exists for today, but the regular starter isn't in it.
        stand_in = _make_player(db_session, 2, "Stand-in", home)
        db_session.add(MatchLineup(match_id=today.id, team_id=home.id, player_id=stand_in.id, is_starter=True))
        db_session.commit()

        impact = team_absence_impact(db_session, home.id, BASE, match=today)
        assert impact.missing_players == ["Regular Starter"]

    def test_no_surprise_detection_before_a_lineup_is_confirmed(self, db_session, clubs):
        """An empty confirmed_starters set means 'not fetched yet', not
        'started nobody' -- must never be read as every regular missing."""

        home, away = clubs
        regular = _make_player(db_session, 1, "Regular Starter", home)
        for i in range(8):
            match = _make_match(db_session, home, away, days_ago=i + 10)
            db_session.add(MatchLineup(match_id=match.id, team_id=home.id, player_id=regular.id, is_starter=True))
        db_session.commit()

        today = _make_match(db_session, home, away, days_ago=0, status="SCHEDULED")
        impact = team_absence_impact(db_session, home.id, BASE, match=today)
        assert impact.missing_players == []

    def test_a_player_reported_absent_and_missing_from_the_confirmed_xi_is_not_double_counted(self, db_session, clubs):
        home, away = clubs
        player = _make_player(db_session, 1, "Star Striker", home)
        for i in range(5):
            match = _make_match(db_session, home, away, days_ago=i + 10)
            db_session.add(MatchLineup(match_id=match.id, team_id=home.id, player_id=player.id, is_starter=True))
        db_session.commit()

        today = _make_match(db_session, home, away, days_ago=0, status="SCHEDULED")
        stand_in = _make_player(db_session, 2, "Stand-in", home)
        db_session.add(MatchLineup(match_id=today.id, team_id=home.id, player_id=stand_in.id, is_starter=True))
        db_session.add(PlayerAbsence(player_id=player.id, team_id=home.id, match_id=today.id, reason="Injury"))
        db_session.commit()

        impact = team_absence_impact(db_session, home.id, BASE, match=today)
        assert impact.missing_players == ["Star Striker"]
        assert impact.strength_lost == pytest.approx(1.0)


class TestAbsenceProbabilityNudge:
    def test_zero_with_no_impact(self):
        assert absence_probability_nudge(AbsenceImpact(), AbsenceImpact()) == 0.0

    def test_favors_home_when_away_is_weakened(self):
        shift = absence_probability_nudge(AbsenceImpact(), AbsenceImpact(strength_lost=1.0))
        assert shift > 0

    def test_favors_away_when_home_is_weakened(self):
        shift = absence_probability_nudge(AbsenceImpact(strength_lost=1.0), AbsenceImpact())
        assert shift < 0

    def test_is_bounded_regardless_of_how_large_the_imbalance_is(self):
        shift = absence_probability_nudge(AbsenceImpact(), AbsenceImpact(strength_lost=50.0))
        assert shift == pytest.approx(MAX_PROBABILITY_SHIFT)

    def test_symmetric_absences_cancel_out(self):
        shift = absence_probability_nudge(AbsenceImpact(strength_lost=0.8), AbsenceImpact(strength_lost=0.8))
        assert shift == pytest.approx(0.0)
