from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_team_or_404
from app.api.schemas import TeamOut
from app.api.serializers import team_to_schema
from app.db.models import Team

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
