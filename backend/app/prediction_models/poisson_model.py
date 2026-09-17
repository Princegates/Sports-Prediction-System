"""Poisson goal model (spec section 23, Model 2/3) with a Dixon-Coles
low-score correction.

Team attack/defense strengths are estimated relative to the league's
home/away scoring averages (a standard, well-documented approach -- see
Maher 1982, Dixon & Coles 1997), split by home/away context per spec
section 9. Everything here is a pure function of data available strictly
before ``as_of``, so it plugs directly into the leakage-free backtester.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.db.models import Match

MAX_GOALS = 8


@dataclass
class GoalMarkets:
    home_win: float
    draw: float
    away_win: float
    over_probabilities: dict[str, float]  # "0.5" -> P(total goals > 0.5), etc.
    btts_yes: float
    btts_no: float
    correct_score_probabilities: dict[str, float]  # top-N scorelines
    most_likely_score: str
    most_likely_score_probability: float
    lambda_home: float
    lambda_away: float
    matrix: list[list[float]] = field(repr=False, default_factory=list)


def _finished_matches_before(db: Session, league: str, as_of) -> list[Match]:
    return list(
        db.execute(
            select(Match).where(
                and_(Match.league == league, Match.date < as_of, Match.home_score.is_not(None))
            )
        ).scalars()
    )


def league_goal_averages(db: Session, league: str, as_of) -> tuple[float, float]:
    matches = _finished_matches_before(db, league, as_of)
    if not matches:
        # Reasonable top-flight European default when there's no history yet.
        return 1.45, 1.15
    home_goals = sum(m.home_score for m in matches)
    away_goals = sum(m.away_score for m in matches)
    n = len(matches)
    return home_goals / n, away_goals / n


def team_attack_defense(
    db: Session, team_id: int, league: str, as_of, avg_home_goals: float, avg_away_goals: float
) -> tuple[float, float, float, float]:
    """Returns (home_attack, home_defense, away_attack, away_defense)."""

    matches = _finished_matches_before(db, league, as_of)
    home_matches = [m for m in matches if m.home_team_id == team_id]
    away_matches = [m for m in matches if m.away_team_id == team_id]

    def ratio(values: list[int], league_avg: float) -> float:
        if not values or league_avg <= 0:
            return 1.0
        return max((sum(values) / len(values)) / league_avg, 0.05)

    home_attack = ratio([m.home_score for m in home_matches], avg_home_goals)
    home_defense = ratio([m.away_score for m in home_matches], avg_away_goals)
    away_attack = ratio([m.away_score for m in away_matches], avg_away_goals)
    away_defense = ratio([m.home_score for m in away_matches], avg_home_goals)

    return home_attack, home_defense, away_attack, away_defense


def expected_goals(
    db: Session, home_team_id: int, away_team_id: int, league: str, as_of
) -> tuple[float, float]:
    avg_home_goals, avg_away_goals = league_goal_averages(db, league, as_of)

    h_attack, h_defense, _, _ = team_attack_defense(db, home_team_id, league, as_of, avg_home_goals, avg_away_goals)
    _, _, a_attack, a_defense = team_attack_defense(db, away_team_id, league, as_of, avg_home_goals, avg_away_goals)

    lambda_home = avg_home_goals * h_attack * a_defense
    lambda_away = avg_away_goals * a_attack * h_defense

    # Keep expectations in a sane range even for teams with almost no history.
    return max(min(lambda_home, 6.0), 0.1), max(min(lambda_away, 6.0), 0.1)


def _poisson_pmf(k: int, lam: float) -> float:
    return math.exp(-lam) * lam**k / math.factorial(k)


def _dixon_coles_tau(home_goals: int, away_goals: int, lambda_home: float, lambda_away: float, rho: float) -> float:
    """Low-score dependence correction from Dixon & Coles (1997)."""

    if home_goals == 0 and away_goals == 0:
        return 1 - lambda_home * lambda_away * rho
    if home_goals == 0 and away_goals == 1:
        return 1 + lambda_home * rho
    if home_goals == 1 and away_goals == 0:
        return 1 + lambda_away * rho
    if home_goals == 1 and away_goals == 1:
        return 1 - rho
    return 1.0


def build_score_matrix(lambda_home: float, lambda_away: float, rho: float = -0.05, max_goals: int = MAX_GOALS) -> list[list[float]]:
    matrix = [[0.0] * (max_goals + 1) for _ in range(max_goals + 1)]
    for h in range(max_goals + 1):
        for a in range(max_goals + 1):
            p = _poisson_pmf(h, lambda_home) * _poisson_pmf(a, lambda_away)
            p *= _dixon_coles_tau(h, a, lambda_home, lambda_away, rho)
            matrix[h][a] = max(p, 0.0)

    total = sum(sum(row) for row in matrix)
    if total > 0:
        matrix = [[p / total for p in row] for row in matrix]
    return matrix


def markets_from_matrix(matrix: list[list[float]], lambda_home: float, lambda_away: float, top_scores: int = 8) -> GoalMarkets:
    max_goals = len(matrix) - 1

    home_win = draw = away_win = 0.0
    btts_yes = 0.0
    goal_line_totals = {"0.5": 0.0, "1.5": 0.0, "2.5": 0.0, "3.5": 0.0, "4.5": 0.0}
    score_probs: dict[str, float] = {}

    for h in range(max_goals + 1):
        for a in range(max_goals + 1):
            p = matrix[h][a]
            score_probs[f"{h}-{a}"] = p
            if h > a:
                home_win += p
            elif h == a:
                draw += p
            else:
                away_win += p
            if h >= 1 and a >= 1:
                btts_yes += p
            total_goals = h + a
            for line in goal_line_totals:
                if total_goals > float(line):
                    goal_line_totals[line] += p

    top_sorted = dict(sorted(score_probs.items(), key=lambda kv: kv[1], reverse=True)[:top_scores])
    most_likely_score, most_likely_prob = max(score_probs.items(), key=lambda kv: kv[1])

    def clamp01(x: float) -> float:
        return min(1.0, max(0.0, x))

    return GoalMarkets(
        home_win=clamp01(home_win),
        draw=clamp01(draw),
        away_win=clamp01(away_win),
        over_probabilities={k: clamp01(v) for k, v in goal_line_totals.items()},
        btts_yes=clamp01(btts_yes),
        btts_no=clamp01(1.0 - btts_yes),
        correct_score_probabilities=top_sorted,
        most_likely_score=most_likely_score,
        most_likely_score_probability=most_likely_prob,
        lambda_home=lambda_home,
        lambda_away=lambda_away,
        matrix=matrix,
    )


def predict(db: Session, home_team_id: int, away_team_id: int, league: str, as_of) -> GoalMarkets:
    lambda_home, lambda_away = expected_goals(db, home_team_id, away_team_id, league, as_of)
    matrix = build_score_matrix(lambda_home, lambda_away)
    return markets_from_matrix(matrix, lambda_home, lambda_away)
