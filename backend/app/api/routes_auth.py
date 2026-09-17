from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.api.rate_limit import enforce
from app.api.schemas import (
    AccountStatusOut,
    LoginIn,
    MatchHistoryOut,
    PreferencesIn,
    RegisterIn,
    RegisterOut,
    TokenOut,
    UserOut,
)
from app.api.serializers import match_view_to_schema, user_to_schema
from app.auth.passwords import hash_password, verify_password
from app.auth.tokens import create_token
from app.config import get_settings
from app.db.models import AuditLog, Match, MatchView, User

router = APIRouter(prefix="/api/auth", tags=["auth"])

PENDING_MESSAGE = (
    "Your account is registered and waiting for a superadmin to review it. You'll be able to sign in "
    "as soon as it's approved."
)


@router.post("/register", response_model=RegisterOut)
def register(payload: RegisterIn, request: Request, db: Session = Depends(get_db)) -> RegisterOut:
    settings = get_settings()
    enforce(
        request,
        "register",
        settings.register_rate_limit_attempts,
        settings.register_rate_limit_window_seconds,
    )

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

    db.add(
        AuditLog(
            actor_user_id=user.id,
            actor_email=user.email,
            action="account.registered",
            target_user_id=user.id,
            detail={"payment_reference": user.payment_reference},
        )
    )
    db.commit()

    return RegisterOut(
        message=(
            "Account created. A superadmin needs to confirm your payment and approve the account "
            "before you can log in."
        ),
        user=user_to_schema(user),
    )


@router.post("/login", response_model=TokenOut)
def login(payload: LoginIn, request: Request, db: Session = Depends(get_db)) -> TokenOut:
    settings = get_settings()
    enforce(
        request,
        "login",
        settings.login_rate_limit_attempts,
        settings.login_rate_limit_window_seconds,
    )

    email = payload.email.strip().lower()
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()

    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect email or password")

    if user.status != "active":
        detail = PENDING_MESSAGE if user.status == "pending" else "Your account has been suspended."
        raise HTTPException(status_code=403, detail=detail)

    token = create_token({"user_id": user.id, "role": user.role}, settings.secret_key, settings.session_ttl_seconds)
    return TokenOut(access_token=token, user=user_to_schema(user))


@router.post("/status", response_model=AccountStatusOut)
def account_status(payload: LoginIn, request: Request, db: Session = Depends(get_db)) -> AccountStatusOut:
    """Where a registration stands, for the "waiting for approval" screen.

    Requires the password, so it reveals nothing to someone who only knows an
    email address, and an unknown email gets the same ``pending`` shape as a
    real one -- otherwise this endpoint would be a way to discover which
    addresses have accounts. Rate-limited on the login bucket for the same
    reason login is.
    """

    settings = get_settings()
    enforce(
        request,
        "login",
        settings.login_rate_limit_attempts,
        settings.login_rate_limit_window_seconds,
    )

    email = payload.email.strip().lower()
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()

    if user is None or not verify_password(payload.password, user.password_hash):
        return AccountStatusOut(status="pending", message=PENDING_MESSAGE)

    if user.status == "active":
        return AccountStatusOut(
            status="active",
            message="Your account is approved and active. You can sign in.",
            submitted_at=user.created_at,
        )
    if user.status == "suspended":
        return AccountStatusOut(
            status="suspended",
            message="This account has been suspended. Contact the administrator for details.",
            submitted_at=user.created_at,
        )
    return AccountStatusOut(status="pending", message=PENDING_MESSAGE, submitted_at=user.created_at)


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
