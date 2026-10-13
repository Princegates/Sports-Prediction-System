"""Poisson corners model.

Same team-strength estimation ``poisson_model`` uses for goals (attack/defense
ratios relative to a league average, time-decayed, shrunk toward 1.0 for
teams with little history), applied to corners instead. No Dixon-Coles
correction is needed here -- that correction exists because real football
scorelines diverge from independent Poisson specifically at low goal counts;
nothing analogous has been fitted for corners, so this is a plain two
independent-Poisson model.

Corners history comes only from football-data.co.uk
(``backend/scripts/enrich_match_stats.py``), so it exists for a handful of
leagues and only for matches already played -- see ``Match.home_corners``.
Every function below returns ``None`` rather than a league-average guess when
a league has no corners history recorded yet, so a league that hasn't been
backfilled simply gets no corners markets instead of invented ones -- the
same reasoning ``app.outcomes.registry``'s own docstring already gives for
why corners were left out before real data existed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.db.models import Match
from app.prediction_models.poisson_model import SHRINKAGE_MATCHES, decay_weight_expr

MAX_CORNERS = 20
TOTAL_CORNERS_LINES = ("7.5", "8.5", "9.5", "10.5", "11.5")


@dataclass
class CornersMarkets:
    lambda_home: float
    lambda_away: float
    over_probabilities: dict[str, float]  # "9.5" -> P(total corners > 9.5), etc.
    home_most_corners: float
    away_most_corners: float
    equal_corners: float


def _finished_with_corners_before(league: str, as_of):
    return and_(
        Match.league == league,
        Match.date < as_of,
        Match.home_corners.is_not(None),
        Match.away_corners.is_not(None),
    )


def league_corners_averages(db: Session, league: str, as_of) -> tuple[float, float] | None:
    """Time-weighted mean corners won by the home and away side in this
    league so far, or ``None`` if this league has no corners history before
    ``as_of`` at all."""

    weight = decay_weight_expr(as_of)
    home_corners, away_corners, total_weight = db.execute(
        select(
            func.sum(weight * Match.home_corners),
            func.sum(weight * Match.away_corners),
            func.sum(weight),
        ).where(_finished_with_corners_before(league, as_of))
    ).one()

    if not total_weight:
        return None
    return float(home_corners) / float(total_weight), float(away_corners) / float(total_weight)


def team_corners_attack_defense(
    db: Session, team_id: int, league: str, as_of, avg_home_corners: float, avg_away_corners: float
) -> tuple[float, float, float, float]:
    """Returns (home_attack, home_defense, away_attack, away_defense), each a
    time-weighted corners rate relative to the league average, shrunk toward
    1.0 by ``SHRINKAGE_MATCHES`` pseudo-matches -- identical shape to
    ``poisson_model.team_attack_defense``, just summing corners columns."""

    at_home = Match.home_team_id == team_id
    at_away = Match.away_team_id == team_id
    weight = decay_weight_expr(as_of)

    (
        home_weight, earned_home, conceded_home,
        away_weight, earned_away, conceded_away,
    ) = db.execute(
        select(
            func.sum(case((at_home, weight), else_=0.0)),
            func.sum(case((at_home, weight * Match.home_corners), else_=0.0)),
            func.sum(case((at_home, weight * Match.away_corners), else_=0.0)),
            func.sum(case((at_away, weight), else_=0.0)),
            func.sum(case((at_away, weight * Match.away_corners), else_=0.0)),
            func.sum(case((at_away, weight * Match.home_corners), else_=0.0)),
        ).where(and_(_finished_with_corners_before(league, as_of), or_(at_home, at_away)))
    ).one()

    def ratio(weighted: float | None, total: float | None, league_avg: float) -> float:
        if league_avg <= 0:
            return 1.0
        value = float(weighted or 0.0) + SHRINKAGE_MATCHES * league_avg
        weights = float(total or 0.0) + SHRINKAGE_MATCHES
        if weights <= 0:
            return 1.0
        return max((value / weights) / league_avg, 0.05)

    return (
        ratio(earned_home, home_weight, avg_home_corners),
        ratio(conceded_home, home_weight, avg_away_corners),
        ratio(earned_away, away_weight, avg_away_corners),
        ratio(conceded_away, away_weight, avg_home_corners),
    )


def expected_corners(
    db: Session, home_team_id: int, away_team_id: int, league: str, as_of
) -> tuple[float, float] | None:
    """(lambda_home, lambda_away) corners rates, or ``None`` if ``league``
    has no corners history before ``as_of`` to estimate them from."""

    averages = league_corners_averages(db, league, as_of)
    if averages is None:
        return None
    avg_home_corners, avg_away_corners = averages

    h_attack, h_defense, _, _ = team_corners_attack_defense(db, home_team_id, league, as_of, avg_home_corners, avg_away_corners)
    _, _, a_attack, a_defense = team_corners_attack_defense(db, away_team_id, league, as_of, avg_home_corners, avg_away_corners)

    lambda_home = avg_home_corners * h_attack * a_defense
    lambda_away = avg_away_corners * a_attack * h_defense

    # Keep expectations sane even for teams with almost no corners history.
    return max(min(lambda_home, 12.0), 1.0), max(min(lambda_away, 12.0), 1.0)


def _poisson_pmf(k: int, lam: float) -> float:
    return math.exp(-lam) * lam**k / math.factorial(k)


def build_corners_matrix(lambda_home: float, lambda_away: float, max_corners: int = MAX_CORNERS) -> list[list[float]]:
    matrix = [[0.0] * (max_corners + 1) for _ in range(max_corners + 1)]
    for h in range(max_corners + 1):
        for a in range(max_corners + 1):
            matrix[h][a] = _poisson_pmf(h, lambda_home) * _poisson_pmf(a, lambda_away)

    total = sum(sum(row) for row in matrix)
    if total > 0:
        matrix = [[p / total for p in row] for row in matrix]
    return matrix


def markets_from_corners_matrix(matrix: list[list[float]], lambda_home: float, lambda_away: float) -> CornersMarkets:
    max_corners = len(matrix) - 1
    home_most = away_most = equal = 0.0
    totals = {line: 0.0 for line in TOTAL_CORNERS_LINES}

    for h in range(max_corners + 1):
        for a in range(max_corners + 1):
            p = matrix[h][a]
            if h > a:
                home_most += p
            elif h < a:
                away_most += p
            else:
                equal += p
            total = h + a
            for line in TOTAL_CORNERS_LINES:
                if total > float(line):
                    totals[line] += p

    def clamp01(x: float) -> float:
        return min(1.0, max(0.0, x))

    return CornersMarkets(
        lambda_home=lambda_home,
        lambda_away=lambda_away,
        over_probabilities={k: clamp01(v) for k, v in totals.items()},
        home_most_corners=clamp01(home_most),
        away_most_corners=clamp01(away_most),
        equal_corners=clamp01(equal),
    )


def predict(db: Session, home_team_id: int, away_team_id: int, league: str, as_of) -> CornersMarkets | None:
    expected = expected_corners(db, home_team_id, away_team_id, league, as_of)
    if expected is None:
        return None
    lambda_home, lambda_away = expected
    matrix = build_corners_matrix(lambda_home, lambda_away)
    return markets_from_corners_matrix(matrix, lambda_home, lambda_away)
