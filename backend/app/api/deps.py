from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.models import Match, Team
from app.db.session import get_db

__all__ = ["get_db", "get_match_or_404", "get_team_or_404"]


def get_match_or_404(match_id: int, db: Session = Depends(get_db)) -> Match:
    match = db.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail=f"Match {match_id} not found")
    return match


def get_team_or_404(team_id: int, db: Session = Depends(get_db)) -> Team:
    team = db.get(Team, team_id)
    if team is None:
        raise HTTPException(status_code=404, detail=f"Team {team_id} not found")
    return team
