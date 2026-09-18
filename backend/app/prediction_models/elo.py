"""Elo / team-strength model (spec section 23, Model 1).

Two responsibilities live here:

1. ``rebuild_elo_history`` replays every finished match for a league in
   chronological order, updating a running Elo rating per team and writing a
   before/after snapshot to ``elo_history``. Because it's a strict replay,
   looking up "team X's rating as of date D" later never leaks information
   from matches played on or after D.

2. ``elo_diff_to_1x2`` turns a (home-advantage-adjusted) Elo difference into
   a full 1X2 probability triple using the Davidson (1970) extension of the
   Bradley-Terry paired-comparison model, which adds an explicit "propensity
   to draw" term -- plain Elo's logistic curve only tells you P(not lose).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import EloHistory, Match


# Competitions whose clubs come from several domestic leagues. They are not
# leagues and must never be replayed as one: every team would restart from
# ``start_rating``, and because ``get_rating_before`` takes a team's most
# recent snapshot regardless of competition, a European replay would overwrite
# the domestic rating that took a decade of matches to earn.
#
# These matches have a different job. They are the only evidence this project
# has about how the five domestic leagues compare, and that is what
# ``league_strength`` fits them for.
EUROPEAN_COMPETITIONS = frozenset({"UEFA Champions League", "UEFA Europa League"})


# Davidson (1970) draw parameter. Tuned so that two perfectly matched teams
# (elo_diff == 0) draw ~26% of the time, matching typical top-flight rates:
# nu / (2 + nu) = 0.26  =>  nu ~= 0.70
DEFAULT_NU = 0.70


@dataclass
class Elo1X2:
    home_win: float
    draw: float
    away_win: float


def expected_score(rating_a: float, rating_b: float) -> float:
    """Classic Elo expectation: P(A does not lose), roughly speaking."""

    return 1.0 / (1.0 + 10 ** (-(rating_a - rating_b) / 400.0))


def _margin_of_victory_multiplier(goal_diff: int, elo_diff_pre: float) -> float:
    """FiveThirtyEight-style multiplier so a 4-0 moves ratings more than a
    1-0, while damping the effect when the result was already expected."""

    if goal_diff == 0:
        return 1.0
    return math.log(abs(goal_diff) + 1) * (2.2 / (abs(elo_diff_pre) * 0.001 + 2.2))


def rebuild_elo_history(
    db: Session,
    league: str,
    k_factor: float = 20.0,
    home_advantage: float = 60.0,
    start_rating: float = 1500.0,
) -> dict[int, float]:
    """Replay all finished matches for ``league`` and persist Elo history.

    Returns the final rating dict {team_id: rating} for convenience.
    """

    if league in EUROPEAN_COMPETITIONS:
        raise ValueError(
            f"{league} draws clubs from several domestic leagues, so replaying it as one "
            "would restart every team at the base rating and overwrite the domestic "
            "history that get_rating_before reads. Fit league offsets with "
            "app.prediction_models.league_strength instead."
        )

    db.execute(
        delete(EloHistory).where(
            EloHistory.match_id.in_(select(Match.id).where(Match.league == league))
        )
    )

    matches = list(
        db.execute(
            select(Match)
            .where(Match.league == league, Match.home_score.is_not(None))
            .order_by(Match.date.asc())
        ).scalars()
    )

    ratings: dict[int, float] = {}

    for m in matches:
        home_rating = ratings.setdefault(m.home_team_id, start_rating)
        away_rating = ratings.setdefault(m.away_team_id, start_rating)

        elo_diff_pre = (home_rating + home_advantage) - away_rating
        expected_home = expected_score(home_rating + home_advantage, away_rating)

        goal_diff = m.home_score - m.away_score
        if goal_diff > 0:
            actual_home = 1.0
        elif goal_diff == 0:
            actual_home = 0.5
        else:
            actual_home = 0.0

        mov = _margin_of_victory_multiplier(goal_diff, elo_diff_pre)
        delta = k_factor * mov * (actual_home - expected_home)

        new_home = home_rating + delta
        new_away = away_rating - delta

        db.add(EloHistory(team_id=m.home_team_id, match_id=m.id, date=m.date, rating_before=home_rating, rating_after=new_home))
        db.add(EloHistory(team_id=m.away_team_id, match_id=m.id, date=m.date, rating_before=away_rating, rating_after=new_away))

        ratings[m.home_team_id] = new_home
        ratings[m.away_team_id] = new_away

    db.commit()
    return ratings


def get_rating_before(db: Session, team_id: int, as_of, start_rating: float = 1500.0) -> float:
    row = db.execute(
        select(EloHistory.rating_after)
        .where(EloHistory.team_id == team_id, EloHistory.date < as_of)
        .order_by(EloHistory.date.desc())
        .limit(1)
    ).scalar_one_or_none()
    return row if row is not None else start_rating


def elo_diff_to_1x2(elo_diff: float, nu: float = DEFAULT_NU) -> Elo1X2:
    """Davidson (1970) paired-comparison model with an explicit draw term.

    Bradley-Terry strengths are derived from the Elo difference:
    ``pi_home = 10 ** (elo_diff / 400)``, ``pi_away = 1`` (only the ratio
    matters). ``elo_diff`` should already include home advantage, i.e.
    ``(home_rating + home_advantage) - away_rating``.
    """

    pi_home = 10 ** (elo_diff / 400.0)
    pi_away = 1.0
    sqrt_term = math.sqrt(pi_home * pi_away)
    draw_term = nu * sqrt_term
    denom = pi_home + pi_away + draw_term

    return Elo1X2(
        home_win=pi_home / denom,
        draw=draw_term / denom,
        away_win=pi_away / denom,
    )
