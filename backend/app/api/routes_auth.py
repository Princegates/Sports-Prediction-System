from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.access import AccessCodeError, current_grant, issue_signup_trial
from app import app_settings, mailer
from app.api.deps import get_current_user, get_db
from app.api.rate_limit import enforce
from app.api.schemas import (
    AccountStatusOut,
    ChangePasswordIn,
    LoginIn,
    MatchHistoryOut,
    PreferencesIn,
    RegisterIn,
    RegisterOut,
    TokenOut,
    UpdateProfileIn,
    UserOut,
)
from app.api.serializers import match_view_to_schema, user_to_schema
from app.auth.passwords import hash_password, verify_password
from app.auth.tokens import create_token
from app.config import get_settings
from app.db.models import AuditLog, Match, MatchView, User

router = APIRouter(prefix="/api/auth", tags=["auth"])

NO_ACCESS_MESSAGE = (
    "Sign in and redeem an access code to unlock the platform. Codes are issued by a Super Admin once "
    "payment is confirmed outside the platform."
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

    # Checked after the rate limit so hammering a closed form is still
    # throttled, and before anything is written.
    if not app_settings.get_value(db, "registration_open"):
        raise HTTPException(
            status_code=403,
            detail="New registrations are closed at the moment. Please check back later.",
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
        status="active",
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
        )
    )
    db.commit()

    # A brand-new account gets a taste of full access with no code, so it
    # isn't bounced straight to a paywall before it has seen anything --
    # then reverts to the free tier once the trial runs out, same as any
    # other expired grant. Never blocks registration: the astronomically
    # unlikely failure mode (a code-generation collision after five
    # retries) still leaves a perfectly normal account that can redeem a
    # real code later.
    trial_days = 0
    if app_settings.get_value(db, "trial_enabled"):
        trial_days = int(app_settings.get_value(db, "trial_duration_days") or 1)
        try:
            issue_signup_trial(db, user, duration_days=trial_days)
        except AccessCodeError:
            trial_days = 0

    # Best-effort: a bounced welcome email is a courtesy lost, not a code
    # lost, so it never affects the response -- unlike an access-code send,
    # there's nothing here worth reporting back to the caller.
    if mailer.is_configured(db):
        whatsapp = str(app_settings.get_value(db, "contact_whatsapp") or "") or None
        subject, body = mailer.welcome_message(
            mailer.resolve_config(db).site_url or None, trial_days=trial_days, whatsapp=whatsapp
        )
        mailer.send_email(email, subject, body, db=db)

    message = (
        f"Account created. You have full access for the next {trial_days} day{'s' if trial_days != 1 else ''} "
        "to explore -- redeem a code any time to keep it going once the trial ends."
        if trial_days
        else (
            "Account created. Sign in, then redeem your access code to unlock predictions -- if you "
            "haven't arranged payment yet, do that with a Super Admin first."
        )
    )

    return RegisterOut(
        message=message,
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
    if user.status == "suspended":
        raise HTTPException(status_code=403, detail="Your account has been suspended.")

    token = create_token({"user_id": user.id, "role": user.role}, settings.secret_key, settings.session_ttl_seconds)
    return TokenOut(access_token=token, user=user_to_schema(user))


@router.post("/status", response_model=AccountStatusOut)
def account_status(payload: LoginIn, request: Request, db: Session = Depends(get_db)) -> AccountStatusOut:
    """Where an account stands re: platform access, for the "why can't I see
    predictions" screen.

    Requires the password, so it reveals nothing to someone who only knows an
    email address, and an unknown email or wrong password gets the same
    ``no_access`` shape as a real account with no grant -- otherwise this
    endpoint would be a way to discover which addresses have accounts.
    Rate-limited on the login bucket for the same reason login is.
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
        return AccountStatusOut(status="no_access", message=NO_ACCESS_MESSAGE)

    if user.status == "suspended":
        return AccountStatusOut(
            status="suspended",
            message="This account has been suspended. Contact the administrator for details.",
            submitted_at=user.created_at,
        )

    grant = current_grant(db, user)
    if grant is not None and grant.expires_at > dt.datetime.utcnow():
        return AccountStatusOut(
            status="active",
            message=f"Your access is active until {grant.expires_at:%Y-%m-%d}.",
            submitted_at=user.created_at,
        )

    return AccountStatusOut(status="no_access", message=NO_ACCESS_MESSAGE, submitted_at=user.created_at)


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


@router.patch("/profile", response_model=UserOut)
def update_profile(
    payload: UpdateProfileIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> UserOut:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name cannot be empty")
    user.name = name
    db.commit()
    db.refresh(user)
    return user_to_schema(user)


@router.patch("/password", status_code=204)
def change_password(
    payload: ChangePasswordIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Requires the current password, so a hijacked but still-valid session
    token can't be used to lock the real owner out permanently -- an
    attacker who only has the token still needs the password."""

    settings = get_settings()
    enforce(
        request,
        "password_change",
        settings.password_change_rate_limit_attempts,
        settings.password_change_rate_limit_window_seconds,
    )

    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    if len(payload.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    user.password_hash = hash_password(payload.new_password)
    db.add(
        AuditLog(
            actor_user_id=user.id,
            actor_email=user.email,
            action="account.password_changed",
            target_user_id=user.id,
        )
    )
    db.commit()

    if mailer.is_configured(db):
        subject, body = mailer.password_changed_message()
        mailer.send_email(user.email, subject, body, db=db)


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
