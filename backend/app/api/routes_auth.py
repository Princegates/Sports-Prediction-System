from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.api.schemas import LoginIn, MatchHistoryOut, PreferencesIn, RegisterIn, RegisterOut, TokenOut, UserOut
from app.api.serializers import match_view_to_schema, user_to_schema
from app.auth.passwords import hash_password, verify_password
from app.auth.tokens import create_token
from app.config import get_settings
from app.db.models import Match, MatchView, User

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=RegisterOut)
def register(payload: RegisterIn, db: Session = Depends(get_db)) -> RegisterOut:
    email = payload.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="A valid email is required")
    if len(payload.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    existing = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="An account with this email already exists")

    user = User(
        email=email,
        name=payload.name.strip() or email,
        password_hash=hash_password(payload.password),
        role="user",
        status="pending",
        payment_reference=payload.payment_reference,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    return RegisterOut(
        message=(
            "Account created. A superadmin needs to confirm your payment and approve the account "
            "before you can log in."
        ),
        user=user_to_schema(user),
    )


@router.post("/login", response_model=TokenOut)
def login(payload: LoginIn, db: Session = Depends(get_db)) -> TokenOut:
    email = payload.email.strip().lower()
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()

    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect email or password")

    if user.status != "active":
        detail = "Your account is awaiting admin approval." if user.status == "pending" else "Your account has been suspended."
        raise HTTPException(status_code=403, detail=detail)

    settings = get_settings()
    token = create_token({"user_id": user.id, "role": user.role}, settings.secret_key, settings.session_ttl_seconds)
    return TokenOut(access_token=token, user=user_to_schema(user))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> UserOut:
    return user_to_schema(user)


@router.patch("/preferences", response_model=UserOut)
def update_preferences(
    payload: PreferencesIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> UserOut:
    """Persists this user's theme/color-profile choice to their own account
    so it follows them across devices, instead of living only in one
    browser's local storage."""
    if payload.theme is not None:
        user.theme = payload.theme
    if payload.accent_profile is not None:
        user.accent_profile = payload.accent_profile
    db.commit()
    db.refresh(user)
    return user_to_schema(user)


@router.post("/history/{match_id}", status_code=204)
def record_match_view(match_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> None:
    """Upserts a 'recently viewed' entry for this user so their own analysis
    history is personal to their account, not shared across users."""
    if db.get(Match, match_id) is None:
        raise HTTPException(status_code=404, detail="Match not found")

    view = db.execute(
        select(MatchView).where(MatchView.user_id == user.id, MatchView.match_id == match_id)
    ).scalar_one_or_none()
    if view is None:
        db.add(MatchView(user_id=user.id, match_id=match_id))
    else:
        view.viewed_at = dt.datetime.utcnow()
    db.commit()


@router.get("/history", response_model=list[MatchHistoryOut])
def match_history(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[MatchHistoryOut]:
    views = db.execute(
        select(MatchView).where(MatchView.user_id == user.id).order_by(MatchView.viewed_at.desc()).limit(20)
    ).scalars().all()

    result = []
    for view in views:
        match = db.get(Match, view.match_id)
        if match is not None:
            result.append(match_view_to_schema(view, match))
    return result
