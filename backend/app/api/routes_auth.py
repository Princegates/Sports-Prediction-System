from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.access import (
    AccessCodeError,
    current_grant,
    ensure_referral_code,
    grant_referee_bonus,
    issue_signup_trial,
    referral_stats,
)
from app import app_settings, mailer
from app.api.deps import get_current_user, get_db, issue_session_token
from app.api import rate_limit
from app.api.rate_limit import enforce, enforce_key
from app.api.schemas import (
    AccountStatusOut,
    ChangePasswordIn,
    ForgotPasswordIn,
    LoginIn,
    MatchHistoryOut,
    MessageOut,
    PauseIn,
    PreferencesIn,
    ReferralStatsOut,
    RegisterIn,
    RegisterOut,
    ResetPasswordIn,
    TokenIn,
    TokenOut,
    UpdateProfileIn,
    UserOut,
    VerificationStatusOut,
    VerifyEmailOut,
)
from app.api.serializers import match_view_to_schema, user_to_schema
from app.auth.passwords import hash_password, verify_password
from app.auth.tokens import (
    RESET_PASSWORD,
    VERIFY_EMAIL,
    create_purpose_token,
    password_fingerprint,
    verify_purpose_token,
)
from app.config import get_settings
from app.db.models import AccessCode, AuditLog, Match, MatchView, User

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _client_ip(request: Request) -> str | None:
    """Where a request says it came from, for the audit log -- see
    rate_limit.client_ip for which header wins and why it's best-effort."""

    ip = rate_limit.client_ip(request)
    return None if ip == "unknown" else ip


def _account_key(scope: str, email: str) -> str:
    return f"{scope}-account:{email}"


# Verified against when the email has no account, so a miss costs the same
# PBKDF2 work as a wrong password and response time can't be used to tell
# registered addresses from unregistered ones.
_DUMMY_PASSWORD_HASH = hash_password("timing-equalizer-not-a-real-password")


def _password_matches(user: User | None, password: str) -> bool:
    if user is None:
        verify_password(password, _DUMMY_PASSWORD_HASH)
        return False
    return verify_password(password, user.password_hash)


MINIMUM_AGE_YEARS = 18
VERIFY_EMAIL_TTL_SECONDS = 3 * 24 * 3600
RESET_PASSWORD_TTL_SECONDS = 30 * 60


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
    # Per-IP counting can be evaded by forging forwarding headers, so total
    # sign-ups are capped as well (see rate_limit's module docstring).
    enforce_key("register-global", settings.register_global_limit, settings.register_global_window_seconds)

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

    if _verification_link_base(db) is not None:
        # Free access waits for a confirmed inbox (see start_free_access).
        # Best-effort send: if it bounces, the account can ask for another
        # from the app's banner.
        trial_days = _trial_days_on_offer(db)
        subject, body = mailer.verify_email_message(_verification_link(db, user), trial_days=trial_days)
        mailer.send_email(email, subject, body, db=db)
        days = f"{trial_days} day{'s' if trial_days != 1 else ''}"
        message = (
            f"Account created. Check {email} for a confirmation link -- your free {days} of full access "
            "starts as soon as you click it."
            if trial_days
            else f"Account created. Check {email} for a confirmation link to finish setting it up."
        )
        return RegisterOut(message=message, user=user_to_schema(user))

    # No way to send a confirmation link (outbound email or the public site
    # URL isn't configured, or verification is switched off): free access
    # starts right away, as it always used to.
    trial_days, referral_bonus_days = start_free_access(db, user)
    _send_welcome(db, user, trial_days)

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
        message += (
            f" Your referral bonus added {referral_bonus_days} extra day{'s' if referral_bonus_days != 1 else ''}."
        )

    return RegisterOut(
        message=message,
        user=user_to_schema(user),
    )


def _trial_days_on_offer(db: Session) -> int:
    if not app_settings.get_value(db, "trial_enabled"):
        return 0
    return int(app_settings.get_value(db, "trial_duration_days") or 1)


def _verification_link_base(db: Session) -> str | None:
    """The site URL confirmation links point at, or ``None`` when signup
    can't ask for confirmation at all -- verification switched off, no
    outbound email, or no public site URL to build a link from."""

    if not app_settings.get_value(db, "email_verification_required"):
        return None
    if not mailer.is_configured(db):
        return None
    site_url = mailer.resolve_config(db).site_url.strip().rstrip("/")
    return site_url or None


def _verification_link(db: Session, user: User) -> str:
    settings = get_settings()
    token = create_purpose_token(
        VERIFY_EMAIL, {"user_id": user.id, "email": user.email}, settings.secret_key, VERIFY_EMAIL_TTL_SECONDS
    )
    return f"{_verification_link_base(db)}/verify-email?token={token}"


def start_free_access(db: Session, user: User) -> tuple[int, int]:
    """Issue the signup trial and, if this account was referred, its own
    half of the referral bonus. Returns ``(trial_days, referee_bonus_days)``.

    Runs once per account: at email confirmation when signup requires it,
    otherwise straight after registration. Neither grant ever blocks the
    flow -- the astronomically unlikely failure (a code-generation
    collision after five retries) still leaves a perfectly normal account
    that can redeem a real code later. The referrer's half waits for this
    account's first payment (access.reward_referrer_for_first_payment).
    """

    trial_days = _trial_days_on_offer(db)
    if trial_days:
        try:
            issue_signup_trial(db, user, duration_days=trial_days)
        except AccessCodeError:
            trial_days = 0

    referral_bonus_days = 0
    referrer = db.get(User, user.referred_by_user_id) if user.referred_by_user_id else None
    if referrer is not None and app_settings.get_value(db, "referral_enabled"):
        referral_bonus_days = int(app_settings.get_value(db, "referral_bonus_days") or 0)
        if referral_bonus_days > 0:
            try:
                grant_referee_bonus(db, referee=user, referrer=referrer, bonus_days=referral_bonus_days)
            except AccessCodeError:
                referral_bonus_days = 0
    return trial_days, referral_bonus_days


def _send_welcome(db: Session, user: User, trial_days: int) -> None:
    # Best-effort: a bounced welcome email is a courtesy lost, not a code
    # lost, so it never affects the response.
    if mailer.is_configured(db):
        whatsapp = str(app_settings.get_value(db, "contact_whatsapp") or "") or None
        subject, body = mailer.welcome_message(
            mailer.resolve_config(db).site_url or None, trial_days=trial_days, whatsapp=whatsapp
        )
        mailer.send_email(user.email, subject, body, db=db)


@router.post("/verify-email", response_model=VerifyEmailOut)
def verify_email(payload: TokenIn, db: Session = Depends(get_db)) -> VerifyEmailOut:
    """Confirms an address from the emailed link and starts the account's
    free access. Idempotent: a second click just says it's already done."""

    data = verify_purpose_token(payload.token, VERIFY_EMAIL, get_settings().secret_key)
    user = db.get(User, data.get("user_id")) if data else None
    if data is None or user is None or user.email != data.get("email"):
        raise HTTPException(status_code=400, detail="This confirmation link is invalid or has expired. Sign in to get a new one.")

    if user.email_verified_at is not None:
        return VerifyEmailOut(message="Your email is already confirmed.", already_verified=True)

    user.email_verified_at = dt.datetime.utcnow()
    db.add(AuditLog(actor_user_id=user.id, actor_email=user.email, action="account.email_verified", target_user_id=user.id))
    db.commit()

    trial_days, referral_bonus_days = start_free_access(db, user)
    _send_welcome(db, user, trial_days)

    total = trial_days + referral_bonus_days
    message = (
        f"Email confirmed. Your {total} day{'s' if total != 1 else ''} of full access {'has' if total == 1 else 'have'} started."
        if total
        else "Email confirmed."
    )
    return VerifyEmailOut(message=message, already_verified=False, access_days=total)


@router.get("/verification", response_model=VerificationStatusOut)
def verification_status(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> VerificationStatusOut:
    """Whether this account is still waiting on its confirmation link to
    start its free access -- drives the app's "confirm your email" banner.
    False for accounts that already had their trial some other way (signed
    up before confirmation was required, or while it was unavailable)."""

    if user.email_verified_at is not None or _verification_link_base(db) is None:
        return VerificationStatusOut(pending=False)
    had_trial = db.execute(
        select(AccessCode.id).where(AccessCode.assigned_user_id == user.id, AccessCode.kind == "trial").limit(1)
    ).first()
    return VerificationStatusOut(pending=had_trial is None, trial_days=_trial_days_on_offer(db))


@router.post("/resend-verification", status_code=204)
def resend_verification(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> None:
    enforce_key(f"resend_verification-user:{user.id}", 3, 3600)
    if user.email_verified_at is not None:
        raise HTTPException(status_code=400, detail="Your email is already confirmed.")
    if _verification_link_base(db) is None:
        raise HTTPException(status_code=503, detail="Email confirmation isn't available right now.")
    subject, body = mailer.verify_email_message(_verification_link(db, user), trial_days=_trial_days_on_offer(db))
    result = mailer.send_email(user.email, subject, body, db=db)
    if not result.sent:
        raise HTTPException(status_code=502, detail="The email couldn't be sent. Try again in a few minutes.")


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
    # Counted per account, not just per IP, so spreading guesses across
    # forged addresses gets nowhere. Only failures count -- checked here,
    # recorded below.
    account_key = _account_key("login", email)
    rate_limit.raise_if_limited(
        account_key, settings.login_account_failure_limit, settings.login_account_failure_window_seconds
    )
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    ip = _client_ip(request)

    if not _password_matches(user, payload.password):
        rate_limit.check(account_key, settings.login_account_failure_limit, settings.login_account_failure_window_seconds)
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

    return TokenOut(access_token=issue_session_token(user), user=user_to_schema(user))


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
    # Same account-level failure counter as login: this endpoint checks a
    # password too, so it must not be a side door around that limit.
    account_key = _account_key("login", email)
    rate_limit.raise_if_limited(
        account_key, settings.login_account_failure_limit, settings.login_account_failure_window_seconds
    )
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()

    if not _password_matches(user, payload.password):
        rate_limit.check(account_key, settings.login_account_failure_limit, settings.login_account_failure_window_seconds)
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
    """Persists this user's theme/color-profile choice and email
    preferences to their own account so they follow across devices,
    instead of living only in one browser's local storage."""
    if payload.theme is not None:
        user.theme = payload.theme
    if payload.accent_profile is not None:
        user.accent_profile = payload.accent_profile
    if payload.notify_weekly_picks is not None:
        user.notify_weekly_picks = payload.notify_weekly_picks
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


@router.patch("/password", response_model=TokenOut)
def change_password(
    payload: ChangePasswordIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TokenOut:
    """Requires the current password, so a hijacked but still-valid session
    token can't be used to lock the real owner out permanently -- an
    attacker who only has the token still needs the password.

    Signs the account out everywhere else (token_version bump) and returns
    a fresh token for the device that made the change, so a stolen session
    dies with the old password instead of living out its seven days.
    """

    settings = get_settings()
    enforce_key(
        f"password_change-user:{user.id}",
        settings.password_change_rate_limit_attempts,
        settings.password_change_rate_limit_window_seconds,
    )

    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    if len(payload.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    _set_password(db, user, payload.new_password, action="account.password_changed")
    return TokenOut(access_token=issue_session_token(user), user=user_to_schema(user))


def _set_password(db: Session, user: User, new_password: str, *, action: str) -> None:
    user.password_hash = hash_password(new_password)
    user.token_version = (user.token_version or 0) + 1
    db.add(AuditLog(actor_user_id=user.id, actor_email=user.email, action=action, target_user_id=user.id))
    db.commit()
    db.refresh(user)

    if mailer.is_configured(db):
        subject, body = mailer.password_changed_message()
        mailer.send_email(user.email, subject, body, db=db)


FORGOT_PASSWORD_MESSAGE = (
    "If an account exists for that email, a reset link is on its way. It works once, for 30 minutes."
)


@router.post("/forgot-password", response_model=MessageOut)
def forgot_password(payload: ForgotPasswordIn, request: Request, db: Session = Depends(get_db)) -> MessageOut:
    """Emails a single-use reset link. Answers the same way whether or not
    the address has an account, so it can't be used to discover which do."""

    enforce(request, "forgot_password", 10, 3600)
    email = payload.email.strip().lower()
    enforce_key(_account_key("forgot_password", email), 3, 3600)

    if not mailer.is_configured(db):
        raise HTTPException(
            status_code=503,
            detail="Password reset by email isn't set up yet. Message a Super Admin to reset it for you.",
        )

    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    site_url = mailer.resolve_config(db).site_url.strip().rstrip("/")
    if user is not None and user.status != "suspended" and site_url:
        settings = get_settings()
        token = create_purpose_token(
            RESET_PASSWORD,
            {"user_id": user.id, "tv": user.token_version or 0, "pw": password_fingerprint(user.password_hash)},
            settings.secret_key,
            RESET_PASSWORD_TTL_SECONDS,
        )
        subject, body = mailer.password_reset_message(f"{site_url}/reset-password?token={token}")
        mailer.send_email(user.email, subject, body, db=db)
        db.add(AuditLog(actor_user_id=user.id, actor_email=user.email, action="account.password_reset_requested",
                        target_user_id=user.id, detail={"ip": _client_ip(request)}))
        db.commit()
    return MessageOut(message=FORGOT_PASSWORD_MESSAGE)


@router.post("/reset-password", response_model=MessageOut)
def reset_password(payload: ResetPasswordIn, db: Session = Depends(get_db)) -> MessageOut:
    """Sets a new password from an emailed link. The link stops working
    once used: the token is bound to a fingerprint of the old password hash
    and to the session version, and setting the password changes both."""

    data = verify_purpose_token(payload.token, RESET_PASSWORD, get_settings().secret_key)
    user = db.get(User, data.get("user_id")) if data else None
    if (
        data is None
        or user is None
        or user.status == "suspended"
        or data.get("pw") != password_fingerprint(user.password_hash)
        or int(data.get("tv", 0)) != (user.token_version or 0)
    ):
        raise HTTPException(status_code=400, detail="This reset link is invalid, expired or already used. Ask for a new one.")
    if len(payload.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    _set_password(db, user, payload.new_password, action="account.password_reset")
    # A working reset link proves the inbox, so it counts as confirmation.
    if user.email_verified_at is None:
        user.email_verified_at = dt.datetime.utcnow()
        db.commit()
    return MessageOut(message="Password updated. Sign in with your new password.")


@router.post("/sessions/revoke-others", response_model=TokenOut)
def revoke_other_sessions(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> TokenOut:
    """Signs this account out on every other device, keeping this one."""

    user.token_version = (user.token_version or 0) + 1
    db.add(AuditLog(actor_user_id=user.id, actor_email=user.email, action="account.sessions_revoked", target_user_id=user.id))
    db.commit()
    db.refresh(user)
    return TokenOut(access_token=issue_session_token(user), user=user_to_schema(user))


PAUSE_CHOICES_DAYS = (1, 7, 30)


@router.post("/pause", response_model=UserOut)
def pause_account(payload: PauseIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> UserOut:
    """A self-set break from every prediction feature. Can be extended but
    never shortened -- see User.paused_until."""

    if payload.days not in PAUSE_CHOICES_DAYS:
        raise HTTPException(status_code=400, detail=f"Choose one of {', '.join(map(str, PAUSE_CHOICES_DAYS))} days.")
    until = dt.datetime.utcnow() + dt.timedelta(days=payload.days)
    if user.paused_until is None or until > user.paused_until:
        user.paused_until = until
    db.add(AuditLog(actor_user_id=user.id, actor_email=user.email, action="account.paused", target_user_id=user.id,
                    detail={"days": payload.days, "until": user.paused_until.isoformat()}))
    db.commit()
    db.refresh(user)
    return user_to_schema(user)


@router.get("/referrals", response_model=ReferralStatsOut)
def my_referrals(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> ReferralStatsOut:
    stats = referral_stats(db, user)
    return ReferralStatsOut(
        joined=stats["joined"],
        paid=stats["paid"],
        bonus_days_each=int(app_settings.get_value(db, "referral_bonus_days") or 0),
        enabled=bool(app_settings.get_value(db, "referral_enabled")),
    )


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
