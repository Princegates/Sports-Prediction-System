import datetime as dt

from fastapi import APIRouter, Depends
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_team_or_404
from app.api.schemas import TeamOut
from app.api.serializers import team_to_schema
from app.db.models import Match, Team
from app.features.team_stats import compute_team_form

router = APIRouter(prefix="/api/teams", tags=["teams"])


@router.get("", response_model=list[TeamOut])
def list_teams(league: str | None = None, db: Session = Depends(get_db)) -> list[TeamOut]:
    stmt = select(Team)
    if league:
        stmt = stmt.where(Team.league == league)
    teams = db.execute(stmt.order_by(Team.name)).scalars()
    return [team_to_schema(t) for t in teams]


@router.get("/{team_id}", response_model=TeamOut)
def get_team(team: Team = Depends(get_team_or_404)) -> TeamOut:
    return team_to_schema(team)


@router.get("/{team_id}/form")
def get_team_form(team: Team = Depends(get_team_or_404), db: Session = Depends(get_db)) -> dict:
    """A snapshot of this team's form as of right now, using the same
    leakage-free window logic the prediction engine uses (just anchored to
    the current moment instead of a specific match's kickoff)."""

    form = compute_team_form(db, team.id, dt.datetime.utcnow(), team.league)
    return vars(form)


@router.get("/{team_id}/head-to-head/{opponent_id}")
def get_head_to_head(
    team_id: int,
    opponent_id: int,
    limit: int = 10,
    db: Session = Depends(get_db),
) -> list[dict]:
    """Real past meetings between these two teams, most recent first --
    across any competition they've both appeared in in our data."""

    stmt = (
        select(Match)
        .where(
            or_(
                and_(Match.home_team_id == team_id, Match.away_team_id == opponent_id),
                and_(Match.home_team_id == opponent_id, Match.away_team_id == team_id),
            ),
            Match.home_score.is_not(None),
        )
        .order_by(Match.date.desc())
        .limit(limit)
    )
    matches = db.execute(stmt).scalars()
    return [
        {
            "date": m.date.isoformat(),
            "league": m.league,
            "home_team": m.home_team.name,
            "away_team": m.away_team.name,
            "home_score": m.home_score,
            "away_score": m.away_score,
        }
        for m in matches
    ]
