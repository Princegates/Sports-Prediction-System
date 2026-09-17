"""Point-in-time team form features (spec sections 6-9, 19).

Every function here takes an ``as_of`` cutoff and only looks at matches
strictly before it, which is what makes the backtester leakage-free: a
prediction generated for a match dated 2024-03-01 can never see a result
from 2024-03-02 onward, even though that result already sits in the same
``matches`` table.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.db.models import Match


@dataclass
class TeamForm:
    matches_played: int = 0
    points_per_game: float = 0.0
    goals_scored_avg: float = 0.0
    goals_conceded_avg: float = 0.0
    home_goals_scored_avg: float = 0.0
    home_goals_conceded_avg: float = 0.0
    away_goals_scored_avg: float = 0.0
    away_goals_conceded_avg: float = 0.0
    clean_sheet_rate: float = 0.0
    rest_days: float = 7.0
    recent_results: list[str] = field(default_factory=list)  # "W"/"D"/"L", most recent last


def _played_matches(db: Session, team_id: int, as_of: dt.datetime, league: str | None, limit: int | None = None) -> list[Match]:
    conditions = [
        or_(Match.home_team_id == team_id, Match.away_team_id == team_id),
        Match.date < as_of,
        Match.home_score.is_not(None),
    ]
    if league:
        conditions.append(Match.league == league)
    stmt = select(Match).where(and_(*conditions)).order_by(Match.date.desc())
    if limit:
        stmt = stmt.limit(limit)
    return list(db.execute(stmt).scalars())


def compute_team_form(
    db: Session,
    team_id: int,
    as_of: dt.datetime,
    league: str | None = None,
    window: int = 10,
) -> TeamForm:
    matches = _played_matches(db, team_id, as_of, league, limit=window)
    if not matches:
        return TeamForm()

    points = 0
    goals_scored: list[int] = []
    goals_conceded: list[int] = []
    home_gs, home_gc, away_gs, away_gc = [], [], [], []
    clean_sheets = 0
    results: list[str] = []

    for m in matches:
        is_home = m.home_team_id == team_id
        gf = m.home_score if is_home else m.away_score
        ga = m.away_score if is_home else m.home_score
        goals_scored.append(gf)
        goals_conceded.append(ga)
        if is_home:
            home_gs.append(gf)
            home_gc.append(ga)
        else:
            away_gs.append(gf)
            away_gc.append(ga)
        if ga == 0:
            clean_sheets += 1
        if gf > ga:
            points += 3
            results.append("W")
        elif gf == ga:
            points += 1
            results.append("D")
        else:
            results.append("L")

    # Matches are ordered most-recent-first above; reverse for a natural
    # chronological "form string" (oldest -> newest).
    results.reverse()

    last_match_date = matches[0].date
    rest_days = (as_of - last_match_date).total_seconds() / 86400.0

    n = len(matches)
    return TeamForm(
        matches_played=n,
        points_per_game=points / n,
        goals_scored_avg=sum(goals_scored) / n,
        goals_conceded_avg=sum(goals_conceded) / n,
        home_goals_scored_avg=(sum(home_gs) / len(home_gs)) if home_gs else 0.0,
        home_goals_conceded_avg=(sum(home_gc) / len(home_gc)) if home_gc else 0.0,
        away_goals_scored_avg=(sum(away_gs) / len(away_gs)) if away_gs else 0.0,
        away_goals_conceded_avg=(sum(away_gc) / len(away_gc)) if away_gc else 0.0,
        clean_sheet_rate=clean_sheets / n,
        rest_days=min(rest_days, 30.0),
        recent_results=results,
    )


def matches_played_before(db: Session, team_id: int, as_of: dt.datetime, league: str | None = None) -> int:
    conditions = [
        or_(Match.home_team_id == team_id, Match.away_team_id == team_id),
        Match.date < as_of,
        Match.home_score.is_not(None),
    ]
    if league:
        conditions.append(Match.league == league)
    stmt = select(Match.id).where(and_(*conditions))
    return len(list(db.execute(stmt).scalars()))
