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

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.db.models import Match

MAX_GOALS = 8

# Half-time score grids stay much smaller than full-time ones -- more than a
# handful of goals inside 45 minutes is vanishingly rare, and Poisson decay
# already makes anything past this contribute effectively nothing.
HT_MAX_GOALS = 6

# Historically, a bit under half of a match's total goals have already gone
# in by half-time -- second halves run slightly higher-scoring than first
# halves (fatigue, subs, more direct play as the game opens up). This is
# only the fallback for a league with no recorded half-time scores yet;
# ht_goal_fraction fits the real figure from history whenever it can.
DEFAULT_HT_GOAL_FRACTION = 0.45


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


def _finished_before(league: str, as_of):
    """The rows every figure in this module is derived from: this league's
    completed matches, strictly before ``as_of``.

    Kept as a predicate rather than a fetch on purpose. Both callers below
    used to pull these rows into Python and reduce them there; each needs a
    handful of averages, and the database computes those without sending the
    matches. In a single-league backtest that difference was 1.87 million
    rows on the wire versus a few thousand -- on managed Postgres, billed
    egress rather than merely slow.
    """

    return and_(Match.league == league, Match.date < as_of, Match.home_score.is_not(None))


def league_goal_averages(db: Session, league: str, as_of) -> tuple[float, float]:
    """Mean goals scored by the home and away side across the league so far."""

    home_goals, away_goals, played = db.execute(
        select(func.sum(Match.home_score), func.sum(Match.away_score), func.count()).where(
            _finished_before(league, as_of)
        )
    ).one()

    if not played:
        # Reasonable top-flight European default when there's no history yet.
        return 1.45, 1.15
    return float(home_goals) / played, float(away_goals) / played


def team_attack_defense(
    db: Session, team_id: int, league: str, as_of, avg_home_goals: float, avg_away_goals: float
) -> tuple[float, float, float, float]:
    """Returns (home_attack, home_defense, away_attack, away_defense)."""

    at_home = Match.home_team_id == team_id
    at_away = Match.away_team_id == team_id

    # Four means in one round trip. CASE rather than the tidier FILTER
    # clause because SQLite only supports FILTER from 3.30, and SQLite is
    # what the project runs on by default.
    scored_home, conceded_home, scored_away, conceded_away = db.execute(
        select(
            func.avg(case((at_home, Match.home_score))),
            func.avg(case((at_home, Match.away_score))),
            func.avg(case((at_away, Match.away_score))),
            func.avg(case((at_away, Match.home_score))),
        ).where(and_(_finished_before(league, as_of), or_(at_home, at_away)))
    ).one()

    def ratio(mean: float | None, league_avg: float) -> float:
        # AVG over no rows is NULL, which is the "this team has never played
        # in this context" case -- treat it as league-average rather than
        # inventing a number.
        if mean is None or league_avg <= 0:
            return 1.0
        return max(float(mean) / league_avg, 0.05)

    return (
        ratio(scored_home, avg_home_goals),
        ratio(conceded_home, avg_away_goals),
        ratio(scored_away, avg_away_goals),
        ratio(conceded_away, avg_home_goals),
    )


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


def ht_goal_fraction(db: Session, league: str, as_of) -> float:
    """What share of a match's full-time goals have typically gone in by
    half-time, fit from this league's own history rather than assumed --
    some leagues genuinely start slower or faster than others. Only matches
    with a recorded half-time score count -- not every provider/season
    captures it (see Match.ht_home_score) -- one missing it is excluded
    rather than treated as 0-0 at the break, which would bias the fraction
    downward for no real reason.
    """

    ht_goals, ft_goals = db.execute(
        select(
            func.sum(Match.ht_home_score + Match.ht_away_score),
            func.sum(Match.home_score + Match.away_score),
        ).where(
            _finished_before(league, as_of),
            Match.ht_home_score.is_not(None),
            Match.ht_away_score.is_not(None),
        )
    ).one()

    if not ft_goals:
        return DEFAULT_HT_GOAL_FRACTION

    fraction = float(ht_goals) / float(ft_goals)
    # A handful of rows with a data-entry slip (an HT score exceeding the FT
    # one, say) must not swing the whole league's split to something
    # implausible -- keep it within a sane real-football range.
    return min(max(fraction, 0.25), 0.65)


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
