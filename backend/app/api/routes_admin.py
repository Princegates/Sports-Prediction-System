from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.access import (
    AccessCodeError,
    access_code_effective_status,
    create_access_code,
    extend_grant,
    revoke_access_code,
    revoke_current_grant,
)
from app.api.deps import get_db, get_match_or_404, require_superadmin
from app.api.schemas import (
    AccessCodeCreatedOut,
    AccessCodeCreateIn,
    AccessCodeOut,
    AdminOverviewOut,
    AdminPickIn,
    AdminPickOut,
    AdminUserOut,
    AuditLogOut,
    ExtendGrantIn,
    FeaturedPickOut,
    FeaturePickIn,
    MatchOut,
    RevokeCodeIn,
    RevokeGrantIn,
)
from app.api.serializers import access_code_to_schema, admin_pick_to_schema, admin_user_to_schema, featured_pick_to_schema, match_to_schema
from app.betcode.selection import price_legs
from app.config import get_settings
from app import mailer
from app.db.models import AccessCode, AccessGrant, AdminPick, AuditLog, ChatMessage, FeaturedPick, LivePrediction, Match, Prediction, User
from app.outcomes.registry import find_outcome

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_superadmin)])


def _record(
    db: Session,
    actor: User,
    action: str,
    target: User | None = None,
    detail: dict | None = None,
) -> None:
    """Append to the audit log. ``actor_email`` is denormalized on purpose:
    an audit entry needs to stay readable after the acting account is renamed
    or deleted, which a foreign key alone won't give you."""

    db.add(
        AuditLog(
            actor_user_id=actor.id,
            actor_email=actor.email,
            action=action,
            target_user_id=target.id if target else None,
            detail=detail,
        )
    )


@router.get("/overview", response_model=AdminOverviewOut)
def overview(db: Session = Depends(get_db)) -> AdminOverviewOut:
    """Headline counts for the admin dashboard -- above all how many active
    accounts have no live access grant, since that's the new attention queue
    now that every account can log in immediately after registering."""

    def count_users(**filters) -> int:
        stmt = select(func.count()).select_from(User)
        for column, value in filters.items():
            stmt = stmt.where(getattr(User, column) == value)
        return db.execute(stmt).scalar() or 0

    now = dt.datetime.utcnow()
    active_users = count_users(status="active")
    active_grants = db.execute(
        select(func.count(func.distinct(AccessGrant.user_id))).where(
            AccessGrant.status == "active", AccessGrant.expires_at > now
        )
    ).scalar() or 0
    return AdminOverviewOut(
        active_users=active_users,
        suspended_users=count_users(status="suspended"),
        superadmins=count_users(role="superadmin"),
        total_users=count_users(),
        users_without_access=max(active_users - active_grants, 0),
        active_access_grants=active_grants,
        matches_analyzed=db.execute(
            select(func.count()).select_from(Match).where(Match.home_score.is_not(None))
        ).scalar() or 0,
        predictions_generated=db.execute(select(func.count()).select_from(Prediction)).scalar() or 0,
        upcoming_fixtures=db.execute(
            select(func.count()).select_from(Match).where(Match.date >= now)
        ).scalar() or 0,
        chat_messages=db.execute(select(func.count()).select_from(ChatMessage)).scalar() or 0,
    )


@router.get("/users", response_model=list[AdminUserOut])
def list_users(status: str | None = None, db: Session = Depends(get_db)) -> list[AdminUserOut]:
    stmt = select(User)
    if status:
        stmt = stmt.where(User.status == status)
    users = db.execute(stmt.order_by(User.created_at.desc())).scalars()
    return [admin_user_to_schema(u, db) for u in users]


@router.get("/audit-log", response_model=list[AuditLogOut])
def audit_log(limit: int = 100, db: Session = Depends(get_db)) -> list[AuditLogOut]:
    limit = max(1, min(limit, 500))
    rows = db.execute(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)).scalars()
    return [
        AuditLogOut(
            id=row.id,
            actor_email=row.actor_email,
            action=row.action,
            target_user_id=row.target_user_id,
            detail=row.detail,
            created_at=row.created_at,
        )
        for row in rows
    ]


def _get_target_user(user_id: int, db: Session) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")
    return user


@router.post("/users/{user_id}/suspend", response_model=AdminUserOut)
def suspend_user(user_id: int, admin: User = Depends(require_superadmin), db: Session = Depends(get_db)) -> AdminUserOut:
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="You cannot suspend your own account")
    target = _get_target_user(user_id, db)
    previous_status = target.status
    target.status = "suspended"
    _record(db, admin, "user.suspended", target, {"from_status": previous_status})
    db.commit()
    db.refresh(target)
    return admin_user_to_schema(target, db)


@router.post("/users/{user_id}/reinstate", response_model=AdminUserOut)
def reinstate_user(user_id: int, admin: User = Depends(require_superadmin), db: Session = Depends(get_db)) -> AdminUserOut:
    """Undo a suspension. Without this, suspending an account was a one-way
    door -- the only way back was editing the database by hand."""

    target = _get_target_user(user_id, db)
    if target.status == "active":
        raise HTTPException(status_code=400, detail="That account is already active")
    previous_status = target.status
    target.status = "active"
    _record(db, admin, "user.reinstated", target, {"from_status": previous_status})
    db.commit()
    db.refresh(target)
    return admin_user_to_schema(target, db)


@router.post("/users/{user_id}/promote", response_model=AdminUserOut)
def promote_user(user_id: int, admin: User = Depends(require_superadmin), db: Session = Depends(get_db)) -> AdminUserOut:
    target = _get_target_user(user_id, db)
    target.role = "superadmin"
    _record(db, admin, "user.promoted", target, {"to_role": "superadmin"})
    db.commit()
    db.refresh(target)
    return admin_user_to_schema(target, db)


@router.post("/users/{user_id}/demote", response_model=AdminUserOut)
def demote_user(user_id: int, admin: User = Depends(require_superadmin), db: Session = Depends(get_db)) -> AdminUserOut:
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="You cannot demote your own account")
    target = _get_target_user(user_id, db)

    # Belt and braces: leaving zero superadmins means nobody can approve
    # registrations again without direct database access. Over this endpoint
    # the self-demotion guard above already makes that unreachable (the caller
    # is a superadmin, so demoting *someone else* always leaves the caller),
    # but that argument depends on both guards staying in place, and the cost
    # of stating the invariant directly is one query.
    remaining_admins = db.execute(
        select(func.count()).select_from(User).where(User.role == "superadmin", User.id != target.id)
    ).scalar() or 0
    if target.role == "superadmin" and remaining_admins == 0:
        raise HTTPException(
            status_code=400,
            detail="This is the only superadmin -- promote another account before demoting this one.",
        )

    target.role = "user"
    _record(db, admin, "user.demoted", target, {"to_role": "user"})
    db.commit()
    db.refresh(target)
    return admin_user_to_schema(target, db)


# --- Access codes -----------------------------------------------------------


@router.post("/access-codes", response_model=AccessCodeCreatedOut)
def create_code(
    payload: AccessCodeCreateIn,
    admin: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
) -> AccessCodeCreatedOut:
    # The account must already exist. That is not a limitation here, it is the
    # flow: you register, discover you have no access, pay, and are issued a
    # code of your own. Requiring the account also turns a mistyped address
    # into an error now rather than a code nobody can ever redeem.
    assigned_email = payload.assigned_user_email.strip().lower()
    target = db.execute(select(User).where(User.email == assigned_email)).scalar_one_or_none()
    if target is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No account is registered with {assigned_email}. They need to register "
                "first -- anyone can, and registering alone grants no access."
            ),
        )

    try:
        code = create_access_code(
            db,
            admin,
            duration_days=payload.duration_days,
            redemption_limit=1,
            code_expires_in_days=payload.code_expires_in_days,
            assigned_user_id=target.id,
            assigned_email=assigned_email,
            notes=payload.notes,
        )
    except AccessCodeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Delivery is attempted only after the code is safely committed, and its
    # failure is reported rather than raised. Turning a bounced email into a
    # 500 would roll back the code and lose it -- the worst outcome available,
    # since the admin then has neither a code nor a sent message.
    emailed = False
    email_error: str | None = None
    if payload.send_email:
        if not mailer.is_configured():
            email_error = (
                "Email is not configured on this server. Set SMTP_HOST and SMTP_FROM "
                "to enable sending; the code below is still valid."
            )
        else:
            subject, body = mailer.access_code_message(
                code.code, code.duration_days, mailer.resolve_config(db).site_url or None
            )
            result = mailer.send_email(assigned_email, subject, body, db=db)
            emailed = result.sent
            email_error = result.error

    return AccessCodeCreatedOut(
        **access_code_to_schema(code, reveal_full=True).model_dump(),
        emailed=emailed,
        email_error=email_error,
    )


@router.post("/access-codes/{code_id}/resend", response_model=AccessCodeCreatedOut)
def resend_code(
    code_id: int,
    admin: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
) -> AccessCodeCreatedOut:
    """Re-sends an already-issued code's email without creating a new one --
    for a buyer who lost the first message, or whose first send failed
    (a still-unverified sending domain, a typo caught after the fact, a
    server that was down that day) and who shouldn't need a second code."""

    code = db.get(AccessCode, code_id)
    if code is None:
        raise HTTPException(status_code=404, detail=f"Access code {code_id} not found")
    if not code.assigned_email:
        raise HTTPException(status_code=400, detail="This code has no assigned email to resend to.")

    status = access_code_effective_status(code)
    if status != "active":
        raise HTTPException(status_code=400, detail=f"Code is {status}, so it can't be resent.")

    emailed = False
    email_error: str | None = None
    if not mailer.is_configured(db):
        email_error = (
            "Email is not configured on this server. Set SMTP_HOST and SMTP_FROM "
            "to enable sending; the code below is still valid."
        )
    else:
        subject, body = mailer.access_code_message(
            code.code, code.duration_days, mailer.resolve_config(db).site_url or None
        )
        result = mailer.send_email(code.assigned_email, subject, body, db=db)
        emailed = result.sent
        email_error = result.error

    _record(db, admin, "access_code.resent", detail={"access_code_id": code.id})
    db.commit()

    return AccessCodeCreatedOut(
        **access_code_to_schema(code, reveal_full=True).model_dump(),
        emailed=emailed,
        email_error=email_error,
    )


@router.get("/access-codes", response_model=list[AccessCodeOut])
def list_access_codes(db: Session = Depends(get_db)) -> list[AccessCodeOut]:
    codes = db.execute(select(AccessCode).order_by(AccessCode.created_at.desc())).scalars()
    return [access_code_to_schema(c) for c in codes]


@router.post("/access-codes/{code_id}/reveal", response_model=AccessCodeOut)
def reveal_code(
    code_id: int,
    admin: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
) -> AccessCodeOut:
    """Shows a code's full value again after the one-time reveal at creation.

    Masked everywhere else on purpose (see mask_code) -- this is the deliberate
    on-demand exception, for a code someone needs to read out or paste again
    after email delivery failed or wasn't set up, e.g. to send over WhatsApp
    by hand. Audit-logged like any other privileged look at sensitive data,
    rather than just leaving every code permanently in plaintext on the page.
    """

    code = db.get(AccessCode, code_id)
    if code is None:
        raise HTTPException(status_code=404, detail=f"Access code {code_id} not found")
    _record(db, admin, "access_code.revealed", detail={"access_code_id": code.id})
    db.commit()
    return access_code_to_schema(code, reveal_full=True)


@router.post("/access-codes/{code_id}/revoke", response_model=AccessCodeOut)
def revoke_code(
    code_id: int,
    payload: RevokeCodeIn,
    admin: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
) -> AccessCodeOut:
    code = db.get(AccessCode, code_id)
    if code is None:
        raise HTTPException(status_code=404, detail=f"Access code {code_id} not found")
    try:
        code = revoke_access_code(db, admin, code, payload.reason)
    except AccessCodeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return access_code_to_schema(code)


@router.post("/users/{user_id}/access/extend", response_model=AdminUserOut)
def extend_user_access(
    user_id: int,
    payload: ExtendGrantIn,
    admin: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
) -> AdminUserOut:
    target = _get_target_user(user_id, db)
    try:
        extend_grant(db, admin, target, payload.additional_days)
    except AccessCodeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return admin_user_to_schema(target, db)


@router.post("/users/{user_id}/access/revoke", response_model=AdminUserOut)
def revoke_user_access(
    user_id: int,
    payload: RevokeGrantIn,
    admin: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
) -> AdminUserOut:
    target = _get_target_user(user_id, db)
    try:
        revoke_current_grant(db, admin, target, payload.reason)
    except AccessCodeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return admin_user_to_schema(target, db)


# --- Live engine sandbox -----------------------------------------------------


@router.delete("/matches/{match_id}/live-events", response_model=MatchOut)
def clear_match_live_events(
    match: Match = Depends(get_match_or_404),
    admin: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
) -> MatchOut:
    """Undoes every simulated event pushed to a match from the Live tab's
    sandbox (anyone with access can push one -- see LiveEventControls), and
    puts the fixture back to SCHEDULED with no score. Without this, a
    simulated goal is permanent: the sandbox writes straight onto the real
    Match row, so there was previously no way back short of editing the
    database by hand.
    """

    result = db.execute(delete(LivePrediction).where(LivePrediction.match_id == match.id))
    match.status = "SCHEDULED"
    match.home_score = None
    match.away_score = None
    _record(db, admin, "match.live_cleared", detail={"match_id": match.id, "events_cleared": result.rowcount})
    db.commit()
    db.refresh(match)
    return match_to_schema(match)


# --- Guda Picks ---------------------------------------------------------------


def _latest_prediction(db: Session, match_id: int) -> Prediction | None:
    return db.execute(
        select(Prediction).where(Prediction.match_id == match_id).order_by(Prediction.created_at.desc())
    ).scalars().first()


@router.post("/featured-picks", response_model=FeaturedPickOut)
def create_featured_pick(
    payload: FeaturePickIn,
    admin: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
) -> FeaturedPickOut:
    """Promotes one real outcome from a match's own Markets tab onto every
    Dashboard's Guda Picks section. Takes a reference (match + market +
    selection), not a probability -- validated against the match's current
    outcomes here so a typo or a market this match doesn't have enough data
    for is rejected up front, rather than silently showing nothing later.
    """

    match = db.get(Match, payload.match_id)
    if match is None:
        raise HTTPException(status_code=404, detail=f"Match {payload.match_id} not found")

    prediction = _latest_prediction(db, match.id)
    if prediction is None:
        raise HTTPException(status_code=400, detail="This match has no prediction yet, so there's nothing to feature.")

    outcome = find_outcome(prediction, payload.market, payload.selection)
    if outcome is None:
        raise HTTPException(
            status_code=400,
            detail=f"\"{payload.selection}\" is not a real outcome of \"{payload.market}\" for this match right now.",
        )

    duplicate = db.execute(
        select(FeaturedPick).where(
            FeaturedPick.match_id == match.id,
            FeaturedPick.market == payload.market,
            FeaturedPick.selection == payload.selection,
        )
    ).scalars().first()
    if duplicate is not None:
        raise HTTPException(status_code=400, detail="That outcome is already featured.")

    note = (payload.note or "").strip() or None
    if note and len(note) > 280:
        raise HTTPException(status_code=400, detail="Note must be 280 characters or fewer.")

    pick = FeaturedPick(
        match_id=match.id,
        market=payload.market,
        selection=payload.selection,
        note=note,
        created_by_user_id=admin.id,
        expires_at=match.date + dt.timedelta(days=2),
    )
    db.add(pick)
    _record(db, admin, "featured_pick.created", detail={"match_id": match.id, "market": payload.market, "selection": payload.selection})
    db.commit()
    db.refresh(pick)
    return featured_pick_to_schema(pick, match, outcome.probability)


@router.get("/featured-picks", response_model=list[FeaturedPickOut])
def list_featured_picks(db: Session = Depends(get_db)) -> list[FeaturedPickOut]:
    """Every currently-featured pick, including ones near expiry -- for the
    admin panel's own management view. Skips one whose outcome no longer
    resolves (match deleted, or a prediction that no longer carries it)
    rather than erroring the whole list."""

    picks = db.execute(select(FeaturedPick).order_by(FeaturedPick.created_at.desc())).scalars().all()
    out: list[FeaturedPickOut] = []
    for pick in picks:
        match = db.get(Match, pick.match_id)
        if match is None:
            continue
        prediction = _latest_prediction(db, match.id)
        outcome = find_outcome(prediction, pick.market, pick.selection) if prediction else None
        out.append(featured_pick_to_schema(pick, match, outcome.probability if outcome else 0.0))
    return out


@router.delete("/featured-picks/{pick_id}", status_code=204)
def delete_featured_pick(
    pick_id: int,
    admin: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
) -> None:
    pick = db.get(FeaturedPick, pick_id)
    if pick is None:
        raise HTTPException(status_code=404, detail=f"Featured pick {pick_id} not found")
    _record(db, admin, "featured_pick.removed", detail={"featured_pick_id": pick_id, "match_id": pick.match_id})
    db.delete(pick)
    db.commit()


# --- Admin Picks (multi-leg slips) ---------------------------------------


MAX_ADMIN_PICK_LEGS = 30


@router.post("/admin-picks", response_model=AdminPickOut)
def create_admin_pick(
    payload: AdminPickIn,
    admin: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
) -> AdminPickOut:
    """Promotes a whole AI Generation slip -- typically the legs of a
    preview an admin just ran on that page -- onto every Dashboard's Admin
    Picks section. Every leg is re-priced from scratch here rather than
    trusting whatever probability/odds the client last saw, the same reason
    create_featured_pick re-resolves its single outcome; if even one leg no
    longer prices, the whole slip is rejected rather than silently featured
    short of what was actually asked for.
    """

    if not payload.legs:
        raise HTTPException(status_code=400, detail="At least one leg is required.")
    if len(payload.legs) > MAX_ADMIN_PICK_LEGS:
        raise HTTPException(status_code=400, detail=f"{MAX_ADMIN_PICK_LEGS} legs is the most a slip can carry.")

    refs = [(leg.match_id, leg.market, leg.selection) for leg in payload.legs]
    legs, warnings = price_legs(db, refs)
    if len(legs) != len(refs):
        raise HTTPException(
            status_code=400,
            detail="One or more legs couldn't be priced, so the slip wasn't featured: " + " ".join(warnings),
        )

    label = (payload.label or "").strip() or None
    if label and len(label) > 120:
        raise HTTPException(status_code=400, detail="Label must be 120 characters or fewer.")
    note = (payload.note or "").strip() or None
    if note and len(note) > 280:
        raise HTTPException(status_code=400, detail="Note must be 280 characters or fewer.")

    pick = AdminPick(
        legs=[{"match_id": leg.match_id, "market": leg.market, "selection": leg.selection} for leg in legs],
        label=label,
        note=note,
        created_by_user_id=admin.id,
        expires_at=max(leg.kickoff for leg in legs) + dt.timedelta(days=2),
    )
    db.add(pick)
    _record(db, admin, "admin_pick.created", detail={"legs": len(legs)})
    db.commit()
    db.refresh(pick)
    return admin_pick_to_schema(pick, legs)


def _resolve_admin_pick(db: Session, pick: AdminPick) -> AdminPickOut | None:
    """Re-prices every leg live; ``None`` when even one no longer resolves,
    so the whole combo drops out rather than showing a partial slip."""

    refs = [(leg["match_id"], leg["market"], leg["selection"]) for leg in pick.legs]
    legs, _warnings = price_legs(db, refs)
    if len(legs) != len(refs):
        return None
    return admin_pick_to_schema(pick, legs)


@router.get("/admin-picks", response_model=list[AdminPickOut])
def list_admin_picks(db: Session = Depends(get_db)) -> list[AdminPickOut]:
    """Every currently-featured slip, including ones near expiry -- for the
    admin management view. Skips one that no longer fully resolves rather
    than erroring the whole list."""

    picks = db.execute(select(AdminPick).order_by(AdminPick.created_at.desc())).scalars().all()
    out: list[AdminPickOut] = []
    for pick in picks:
        resolved = _resolve_admin_pick(db, pick)
        if resolved is not None:
            out.append(resolved)
    return out


@router.delete("/admin-picks/{pick_id}", status_code=204)
def delete_admin_pick(
    pick_id: int,
    admin: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
) -> None:
    pick = db.get(AdminPick, pick_id)
    if pick is None:
        raise HTTPException(status_code=404, detail=f"Admin pick {pick_id} not found")
    _record(db, admin, "admin_pick.removed", detail={"admin_pick_id": pick_id, "legs": len(pick.legs)})
    db.delete(pick)
    db.commit()
