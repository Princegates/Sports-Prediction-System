from __future__ import annotations

import datetime as dt

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.timeutil import utcnow
from app.access import current_grant
from app.auth.tokens import create_token, verify_token
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
    "require_not_paused",
    "issue_session_token",
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
    # A purpose claim marks an emailed link (confirm email, reset password),
    # never a session -- see auth/tokens.py.
    if payload is None or payload.get("purpose") is not None:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    user = db.get(User, payload.get("user_id"))
    if user is None:
        raise HTTPException(status_code=401, detail="Account no longer exists")
    # Tokens issued before token_version existed carry no "tv" and read as
    # 0, matching every account that has never bumped it.
    if int(payload.get("tv", 0)) != (user.token_version or 0):
        raise HTTPException(status_code=401, detail="This session has been signed out. Please sign in again.")
    if user.status == "suspended":
        raise HTTPException(status_code=403, detail="Account is suspended")
    return user


def issue_session_token(user: User) -> str:
    """A session token for ``user``, stamped with its current token_version
    so bumping that version revokes it."""

    settings = get_settings()
    return create_token(
        {"user_id": user.id, "role": user.role, "tv": user.token_version or 0},
        settings.secret_key,
        settings.session_ttl_seconds,
    )


def require_superadmin(user: User = Depends(get_current_user)) -> User:
    if user.role != "superadmin":
        raise HTTPException(status_code=403, detail="Superadmin access required")
    return user


def _raise_if_paused(user: User, now: dt.datetime) -> None:
    if user.paused_until is not None and user.paused_until > now:
        raise HTTPException(
            status_code=403,
            detail=f"You've paused predictions until {user.paused_until:%d %b %Y, %H:%M} UTC. They'll be back then.",
        )


def require_not_paused(user: User = Depends(get_current_user)) -> User:
    """Login-level routes that still serve picks (the free tier) honour a
    self-set pause too -- a break that left the free picks showing wouldn't
    be one."""

    _raise_if_paused(user, utcnow())
    return user


def require_active_access(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> User:
    """Gates the prediction-serving routers on a live ``AccessGrant``,
    separate from login. A superadmin never needs a redeemed code to do
    their own job of managing the platform."""

    if user.role == "superadmin":
        return user

    now = utcnow()
    _raise_if_paused(user, now)

    grant = current_grant(db, user)
    if grant is None or grant.expires_at <= now:
        raise HTTPException(status_code=403, detail="Your access has expired. Redeem a code to continue.")
    return user
