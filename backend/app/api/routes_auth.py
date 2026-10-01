from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.access import AccessCodeError, current_grant, ensure_referral_code, grant_referral_bonus, issue_signup_trial
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


def _client_ip(request: Request) -> str | None:
    """Same precedence as rate_limit.client_key -- the left-most
    X-Forwarded-For entry when present (any real deployment sits behind a
    proxy), falling back to the direct peer. Client-controlled and therefore
    spoofable like any IP in a header, but still the useful signal for
    spotting where a run of failed logins is coming from."""

    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


MINIMUM_AGE_YEARS = 18


def _age_years(born: dt.date, as_of: dt.date) -> int:
    """Whole years elapsed, correctly handling a birthday that hasn't
    happened yet this year (a naive ``as_of.year - born.year`` overstates
    age by one for anyone whose birthday is still ahead of today)."""

    years = as_of.year - born.year
    if (as_of.month, as_of.day) < (born.month, born.day):
        years -= 1
    return years


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

    today = dt.date.today()
    if payload.date_of_birth > today:
        raise HTTPException(status_code=400, detail="Date of birth cannot be in the future")
    if _age_years(payload.date_of_birth, today) < MINIMUM_AGE_YEARS:
        raise HTTPException(
            status_code=400,
            detail=f"You must be at least {MINIMUM_AGE_YEARS} years old to create an account.",
        )

    existing = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="An account with this email already exists")

    # Resolved before the new account is created, not after: a mistyped
    # code should fail the signup cleanly rather than create the account
    # and only then tell the referee -- and possibly the referrer -- that
    # the bonus they expected silently didn't happen. Ignored entirely
    # (never even looked up) while the program is switched off, so a stale
    # shared link doesn't error out someone who has no idea referrals were
    # ever involved.
    referrer: User | None = None
    referral_enabled = bool(app_settings.get_value(db, "referral_enabled"))
    raw_referral_code = (payload.referral_code or "").strip().upper()
    if referral_enabled and raw_referral_code:
        referrer = db.execute(select(User).where(User.referral_code == raw_referral_code)).scalar_one_or_none()
        if referrer is None:
            raise HTTPException(
                status_code=400,
                detail="That referral code wasn't found -- leave it blank to continue without one.",
            )

    user = User(
        email=email,
        name=payload.name.strip() or email,
        password_hash=hash_password(payload.password),
        date_of_birth=payload.date_of_birth,
        referred_by_user_id=referrer.id if referrer else None,
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

    # Every account gets its own code to hand out from day one, including
    # one that arrived via somebody else's -- there's no reason referring
    # should be one-directional.
    ensure_referral_code(db, user)

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

    # Stacks on top of the trial grant just issued above (see
    # grant_referral_bonus's own docstring on why that's safe) -- never
    # blocks registration, same reasoning as the trial itself: the account
    # the referral code pointed to is real (resolved earlier), so failure
    # here would only be the same astronomically unlikely collision case.
    referral_bonus_days = 0
    if referrer is not None:
        referral_bonus_days = int(app_settings.get_value(db, "referral_bonus_days") or 0)
        if referral_bonus_days > 0:
            try:
                grant_referral_bonus(db, referrer=referrer, referee=user, bonus_days=referral_bonus_days)
            except AccessCodeError:
                referral_bonus_days = 0

    # Best-effort: a bounced welcome email is a courtesy lost, not a code
    # lost, so it never affects the response -- unlike an access-code send,
    # there's nothing here worth reporting back to the caller.
    if mailer.is_configured(db):
        whatsapp = str(app_settings.get_value(db, "contact_whatsapp") or "") or None
        subject, body = mailer.welcome_message(
            mailer.resolve_config(db).site_url or None, trial_days=trial_days, whatsapp=whatsapp
        )
        mailer.send_email(email, subject, body, db=db)

    total_days = trial_days + referral_bonus_days
    message = (
        f"Account created. You have full access for the next {total_days} day{'s' if total_days != 1 else ''} "
        "to explore -- redeem a code any time to keep it going once the trial ends."
        if total_days
        else (
            "Account created. Sign in, then redeem your access code to unlock predictions -- if you "
            "haven't arranged payment yet, do that with a Super Admin first."
        )
    )
    if referral_bonus_days:
        message += f" Your referral bonus added {referral_bonus_days} extra day{'s' if referral_bonus_days != 1 else ''} for both of you."

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
    ip = _client_ip(request)

    if user is None or not verify_password(payload.password, user.password_hash):
        db.add(
            AuditLog(
                actor_user_id=user.id if user else None,
                actor_email=email,
                action="account.login_failed",
                target_user_id=user.id if user else None,
                detail={"ip": ip} if ip else None,
            )
        )
        db.commit()
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    if user.status == "suspended":
        db.add(
            AuditLog(
                actor_user_id=user.id,
                actor_email=user.email,
                action="account.login_blocked",
                target_user_id=user.id,
                detail={"reason": "suspended", "ip": ip} if ip else {"reason": "suspended"},
            )
        )
        db.commit()
        raise HTTPException(status_code=403, detail="Your account has been suspended.")

    db.add(
        AuditLog(
            actor_user_id=user.id,
            actor_email=user.email,
            action="account.login",
            target_user_id=user.id,
            detail={"ip": ip} if ip else None,
        )
    )
    db.commit()

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
    previous_name = user.name
    user.name = name
    if name != previous_name:
        db.add(
            AuditLog(
                actor_user_id=user.id,
                actor_email=user.email,
                action="account.profile_updated",
                target_user_id=user.id,
                detail={"from": previous_name, "to": name},
            )
        )
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
