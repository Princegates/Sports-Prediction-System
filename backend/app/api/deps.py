from __future__ import annotations

import datetime as dt

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.access import current_grant
from app.auth.tokens import verify_token
from app.config import get_settings
from app.db.models import Match, Team, User
from app.db.session import get_db

__all__ = [
    "get_db",
    "get_match_or_404",
    "get_team_or_404",
    "get_current_user",
    "require_superadmin",
    "require_active_access",
]


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


def get_current_user(authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> User:
    """Requires a valid ``Authorization: Bearer <token>`` header for an
    account that isn't suspended. This is the login-level gate only --
    whether the account also holds a live access grant is a separate check
    (``require_active_access``), so a user whose access has lapsed can still
    log in and redeem a new code instead of being locked out of the API
    entirely."""

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")

    token = authorization.removeprefix("Bearer ").strip()
    payload = verify_token(token, get_settings().secret_key)
    if payload is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    user = db.get(User, payload.get("user_id"))
    if user is None:
        raise HTTPException(status_code=401, detail="Account no longer exists")
    if user.status == "suspended":
        raise HTTPException(status_code=403, detail="Account is suspended")
    return user


def require_superadmin(user: User = Depends(get_current_user)) -> User:
    if user.role != "superadmin":
        raise HTTPException(status_code=403, detail="Superadmin access required")
    return user


def require_active_access(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> User:
    """Gates the prediction-serving routers on a live ``AccessGrant``,
    separate from login. A superadmin never needs a redeemed code to do
    their own job of managing the platform."""

    if user.role == "superadmin":
        return user

    grant = current_grant(db, user)
    if grant is None or grant.expires_at <= dt.datetime.utcnow():
        raise HTTPException(status_code=403, detail="Your access has expired. Redeem a code to continue.")
    return user
