from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.timeutil import utcnow
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
    AdminPickResultIn,
    AdminUserOut,
    AuditLogOut,
    AuditLogPageOut,
    ExtendGrantIn,
    FeaturedPickOut,
    FeaturedPickResultIn,
    FeaturedPublicIn,
    FeaturePickIn,
    FeaturePickUpdateIn,
    MatchOut,
    PublicTeaserPickOut,
    RevokeCodeIn,
    RevokeGrantIn,
)
from app.api.routes_public import resolve_public_teaser_pick
from app.api.serializers import access_code_to_schema, admin_pick_to_schema, admin_user_to_schema, featured_pick_to_schema, match_to_schema
from app.betcode.selection import price_legs, resolve_legs_unpriced
from app.config import get_settings
from app import mailer, track_record
from app.db.models import AccessCode, AccessGrant, AdminPick, AuditLog, BookingSlip, ChatMessage, FeaturedPick, LivePrediction, Match, Prediction, User
from app.outcomes.registry import find_outcome
from app.pick_settlement import frozen_admin_pick_legs, settle_admin_pick, settle_booking_slip, settle_featured_pick

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_superadmin)])

EMAIL_NOT_CONFIGURED = (
    "Email is not configured. Set it up under Settings -> Email (an SMTP host and From "
    "address, or a Resend API key and From address); the code below is still valid."
)


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

    now = utcnow()
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


@router.get("/audit-log", response_model=AuditLogPageOut)
def audit_log(
    limit: int = 50,
    offset: int = 0,
    user_id: int | None = None,
    action: str | None = None,
    since: dt.datetime | None = None,
    until: dt.datetime | None = None,
    db: Session = Depends(get_db),
) -> AuditLogPageOut:
    """Filtered, paginated read of the append-only audit log, for the
    dedicated Activity Log page. ``user_id`` matches a row where that
    account is either the actor or the target -- "what did they do" and
    "what happened to them" are both "their activity" from an admin's
    point of view, and most actions only ever populate one of the two
    columns anyway."""

    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    stmt = select(AuditLog)
    if user_id is not None:
        stmt = stmt.where(or_(AuditLog.actor_user_id == user_id, AuditLog.target_user_id == user_id))
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if since is not None:
        stmt = stmt.where(AuditLog.created_at >= since)
    if until is not None:
        stmt = stmt.where(AuditLog.created_at <= until)

    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar() or 0
    rows = db.execute(stmt.order_by(AuditLog.created_at.desc()).offset(offset).limit(limit)).scalars()
    return AuditLogPageOut(
        items=[
            AuditLogOut(
                id=row.id,
                actor_user_id=row.actor_user_id,
                actor_email=row.actor_email,
                action=row.action,
                target_user_id=row.target_user_id,
                detail=row.detail,
                created_at=row.created_at,
            )
            for row in rows
        ],
        total=total,
    )


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
    # Also revokes every session it holds, so reinstating later doesn't
    # quietly revive tokens issued before the suspension.
    target.token_version = (target.token_version or 0) + 1
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
        if not mailer.is_configured(db):
            email_error = EMAIL_NOT_CONFIGURED
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
        email_error = EMAIL_NOT_CONFIGURED
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
        probability_at_pick=outcome.probability,
        note=note,
        created_by_user_id=admin.id,
        expires_at=match.date + dt.timedelta(days=2),
    )
    db.add(pick)
    _record(db, admin, "featured_pick.created", detail={"match_id": match.id, "market": payload.market, "selection": payload.selection})
    db.commit()
    db.refresh(pick)
    return featured_pick_to_schema(pick, match, outcome.probability)


@router.patch("/featured-picks/{pick_id}", response_model=FeaturedPickOut)
def update_featured_pick(
    pick_id: int,
    payload: FeaturePickUpdateIn,
    admin: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
) -> FeaturedPickOut:
    """Edits a Guda Pick's note without unfeaturing and recreating it. The
    match/market/selection it references can't be changed here -- that's a
    different outcome, which belongs behind its own feature/unfeature pair
    so the audit log records it as the promotion it actually is."""

    pick = db.get(FeaturedPick, pick_id)
    if pick is None:
        raise HTTPException(status_code=404, detail=f"Featured pick {pick_id} not found")

    note = (payload.note or "").strip() or None
    if note and len(note) > 280:
        raise HTTPException(status_code=400, detail="Note must be 280 characters or fewer.")

    pick.note = note
    _record(db, admin, "featured_pick.updated", detail={"featured_pick_id": pick_id})
    db.commit()
    db.refresh(pick)

    resolved = _resolve_featured_pick(db, pick)
    if resolved is None:
        raise HTTPException(status_code=404, detail="This pick's match no longer exists.")
    return resolved


def _resolve_featured_pick(db: Session, pick: FeaturedPick) -> FeaturedPickOut | None:
    """Settles the pick first (a no-op once it's already left "pending").
    Still pending -> probability recomputed live from the match's current
    Prediction (0.0 if it no longer resolves -- the admin view shows the
    pick either way, unlike the public guda_picks(), which drops it).
    Settled -> served from the probability_at_pick snapshot instead, never
    re-resolved (the match has finished; live resolution would fail)."""

    match = db.get(Match, pick.match_id)
    if match is None:
        return None

    if settle_featured_pick(db, pick):
        db.commit()

    if pick.result != "pending":
        return featured_pick_to_schema(pick, match, pick.probability_at_pick or 0.0)

    prediction = _latest_prediction(db, match.id)
    outcome = find_outcome(prediction, pick.market, pick.selection) if prediction else None
    return featured_pick_to_schema(pick, match, outcome.probability if outcome else 0.0)


@router.get("/featured-picks", response_model=list[FeaturedPickOut])
def list_featured_picks(db: Session = Depends(get_db)) -> list[FeaturedPickOut]:
    """Every currently-featured pick, including ones near expiry and every
    settled one regardless of expiry -- for the admin panel's own
    management view. Skips one whose match no longer exists rather than
    erroring the whole list."""

    picks = db.execute(select(FeaturedPick).order_by(FeaturedPick.created_at.desc())).scalars().all()
    out: list[FeaturedPickOut] = []
    for pick in picks:
        resolved = _resolve_featured_pick(db, pick)
        if resolved is not None:
            out.append(resolved)
    return out


@router.patch("/featured-picks/{pick_id}/result", response_model=FeaturedPickOut)
def set_featured_pick_result(
    pick_id: int,
    payload: FeaturedPickResultIn,
    admin: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
) -> FeaturedPickOut:
    """Manual settlement override -- the fallback for a market
    app.outcomes.grading doesn't auto-grade (stuck at "unresolved"), or to
    correct a mistake. Setting "pending" un-freezes the pick so the next
    read re-settles it from scratch."""

    pick = db.get(FeaturedPick, pick_id)
    if pick is None:
        raise HTTPException(status_code=404, detail=f"Featured pick {pick_id} not found")
    if payload.result not in {"pending", "won", "lost", "unresolved"}:
        raise HTTPException(status_code=400, detail="result must be one of pending, won, lost, unresolved")

    pick.result = payload.result
    pick.settled_at = None if payload.result == "pending" else utcnow()
    _record(db, admin, "featured_pick.result_set", detail={"featured_pick_id": pick_id, "result": payload.result})
    db.commit()

    resolved = _resolve_featured_pick(db, pick)
    if resolved is None:
        raise HTTPException(status_code=404, detail="This pick's match no longer exists.")
    return resolved


@router.patch("/featured-picks/{pick_id}/featured-public", response_model=FeaturedPickOut)
def set_featured_pick_featured_public(
    pick_id: int,
    payload: FeaturedPublicIn,
    admin: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
) -> FeaturedPickOut:
    """Commit this pick to the public track record (or take it back out)
    -- only before its match kicks off; see app.track_record."""

    pick = db.get(FeaturedPick, pick_id)
    if pick is None:
        raise HTTPException(status_code=404, detail=f"Featured pick {pick_id} not found")
    try:
        track_record.set_featured(db, pick, payload.featured_public)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _record(
        db, admin, "featured_pick.featured_public_set",
        detail={"featured_pick_id": pick_id, "featured_public": payload.featured_public},
    )
    db.commit()

    resolved = _resolve_featured_pick(db, pick)
    if resolved is None:
        raise HTTPException(status_code=404, detail="This pick's match no longer exists.")
    return resolved


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


def _validate_admin_pick_payload(
    payload: AdminPickIn, db: Session
) -> tuple[list, str | None, str | None, str | None, str | None]:
    """Shared by create and update: every leg is re-resolved from scratch
    here rather than trusting whatever probability/odds the client last
    saw (the same reason create_featured_pick re-resolves its single
    outcome), and label/note are re-validated the same way either time.
    Raises HTTPException on any failure -- if even one leg no longer
    resolves, the whole slip is rejected rather than silently featured
    short of what was actually asked for.

    ``payload.priced`` picks which resolver runs: price_legs (requires a
    real MatchOdds row per leg) or resolve_legs_unpriced (model probability
    only, no bookmaker quote required) -- see AdminPickIn's docstring.
    """

    if not payload.legs:
        raise HTTPException(status_code=400, detail="At least one leg is required.")
    if len(payload.legs) > MAX_ADMIN_PICK_LEGS:
        raise HTTPException(status_code=400, detail=f"{MAX_ADMIN_PICK_LEGS} legs is the most a slip can carry.")

    refs = [(leg.match_id, leg.market, leg.selection) for leg in payload.legs]
    if payload.priced:
        legs, warnings = price_legs(db, refs)
        failure = "One or more legs couldn't be priced, so the slip wasn't saved: "
    else:
        legs, warnings = resolve_legs_unpriced(db, refs)
        failure = "One or more legs couldn't be resolved, so the slip wasn't saved: "
    if len(legs) != len(refs):
        raise HTTPException(status_code=400, detail=failure + " ".join(warnings))

    label = (payload.label or "").strip() or None
    if label and len(label) > 120:
        raise HTTPException(status_code=400, detail="Label must be 120 characters or fewer.")
    note = (payload.note or "").strip() or None
    if note and len(note) > 280:
        raise HTTPException(status_code=400, detail="Note must be 280 characters or fewer.")

    booking_code = (payload.booking_code or "").strip() or None
    if booking_code and len(booking_code) > 64:
        raise HTTPException(status_code=400, detail="Booking code must be 64 characters or fewer.")
    booking_code_bookmaker = (payload.booking_code_bookmaker or "").strip() or None
    if booking_code_bookmaker and len(booking_code_bookmaker) > 64:
        raise HTTPException(status_code=400, detail="Bookmaker name must be 64 characters or fewer.")
    if bool(booking_code) != bool(booking_code_bookmaker):
        raise HTTPException(
            status_code=400,
            detail="A booking code needs the bookmaker it's for, and a bookmaker needs a code -- set both or neither.",
        )

    return legs, label, note, booking_code, booking_code_bookmaker


@router.post("/admin-picks", response_model=AdminPickOut)
def create_admin_pick(
    payload: AdminPickIn,
    admin: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
) -> AdminPickOut:
    """Promotes a whole AI Generation slip -- typically the legs of a
    preview an admin just ran on that page -- onto every Dashboard's Admin
    Picks section."""

    legs, label, note, booking_code, booking_code_bookmaker = _validate_admin_pick_payload(payload, db)

    pick = AdminPick(
        legs=[
            {
                "match_id": leg.match_id,
                "market": leg.market,
                "selection": leg.selection,
                # Snapshot, used only once the pick settles and can no
                # longer be re-resolved live -- see frozen_admin_pick_legs.
                "probability_at_pick": leg.model_probability,
                "decimal_odds_at_pick": leg.decimal_odds,
            }
            for leg in legs
        ],
        priced=payload.priced,
        label=label,
        note=note,
        booking_code=booking_code,
        booking_code_bookmaker=booking_code_bookmaker,
        created_by_user_id=admin.id,
        expires_at=max(leg.kickoff for leg in legs) + dt.timedelta(days=2),
    )
    db.add(pick)
    _record(db, admin, "admin_pick.created", detail={"legs": len(legs)})
    db.commit()
    db.refresh(pick)
    return admin_pick_to_schema(pick, legs)


@router.patch("/admin-picks/{pick_id}", response_model=AdminPickOut)
def update_admin_pick(
    pick_id: int,
    payload: AdminPickIn,
    admin: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
) -> AdminPickOut:
    """Replaces an already-featured slip's legs/label/note in place -- same
    validation as creating one (every leg re-priced from scratch), so an
    edited slip can never end up showing something that no longer prices.
    The pick keeps its id and created_at; only what it contains changes,
    including expires_at, which is recomputed from the new legs' kickoffs.
    """

    pick = db.get(AdminPick, pick_id)
    if pick is None:
        raise HTTPException(status_code=404, detail=f"Admin pick {pick_id} not found")

    legs, label, note, booking_code, booking_code_bookmaker = _validate_admin_pick_payload(payload, db)

    pick.legs = [
        {
            "match_id": leg.match_id,
            "market": leg.market,
            "selection": leg.selection,
            "probability_at_pick": leg.model_probability,
            "decimal_odds_at_pick": leg.decimal_odds,
        }
        for leg in legs
    ]
    # The legs just changed, so any previous settlement no longer describes
    # what this pick actually contains -- re-settle from scratch.
    pick.result = "pending"
    pick.leg_results = None
    pick.settled_at = None
    pick.priced = payload.priced
    pick.label = label
    pick.note = note
    pick.booking_code = booking_code
    pick.booking_code_bookmaker = booking_code_bookmaker
    pick.expires_at = max(leg.kickoff for leg in legs) + dt.timedelta(days=2)
    _record(db, admin, "admin_pick.updated", detail={"admin_pick_id": pick_id, "legs": len(legs)})
    db.commit()
    db.refresh(pick)
    return admin_pick_to_schema(pick, legs)


def _resolve_admin_pick(db: Session, pick: AdminPick) -> AdminPickOut | None:
    """Settles the pick first (a no-op once it's already left "pending").
    Still pending -> re-resolves every leg live, priced from MatchOdds when
    ``pick.priced``, by model probability alone otherwise; ``None`` when
    even one no longer resolves, so the whole combo drops out rather than
    showing a partial slip. Settled -> served from its frozen legs instead,
    never re-resolved (the match has finished; live resolution would fail)."""

    if settle_admin_pick(db, pick):
        db.commit()

    if pick.result != "pending":
        return admin_pick_to_schema(pick, frozen_admin_pick_legs(db, pick))

    refs = [(leg["match_id"], leg["market"], leg["selection"]) for leg in pick.legs]
    legs, _warnings = price_legs(db, refs) if pick.priced else resolve_legs_unpriced(db, refs)
    if len(legs) != len(refs):
        return None
    return admin_pick_to_schema(pick, legs)


@router.get("/admin-picks", response_model=list[AdminPickOut])
def list_admin_picks(db: Session = Depends(get_db)) -> list[AdminPickOut]:
    """Every currently-featured slip, including ones near expiry and every
    settled one regardless of expiry -- for the admin management view.
    Skips a still-pending one that no longer fully resolves rather than
    erroring the whole list. Excludes the public teaser pick (if one is
    set) -- that's a different audience, managed from its own
    public-teaser-pick endpoints instead."""

    picks = db.execute(
        select(AdminPick).where(AdminPick.public_teaser.is_(False)).order_by(AdminPick.created_at.desc())
    ).scalars().all()
    out: list[AdminPickOut] = []
    for pick in picks:
        resolved = _resolve_admin_pick(db, pick)
        if resolved is not None:
            out.append(resolved)
    return out


@router.patch("/admin-picks/{pick_id}/result", response_model=AdminPickOut)
def set_admin_pick_result(
    pick_id: int,
    payload: AdminPickResultIn,
    admin: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
) -> AdminPickOut:
    """Manual settlement override -- the fallback for a leg on a market
    app.outcomes.grading doesn't auto-grade (stuck at "unresolved"), or to
    correct a mistake. Setting "pending" un-freezes the pick so the next
    read re-settles it from scratch."""

    pick = db.get(AdminPick, pick_id)
    if pick is None:
        raise HTTPException(status_code=404, detail=f"Admin pick {pick_id} not found")
    if payload.result not in {"pending", "won", "lost", "unresolved"}:
        raise HTTPException(status_code=400, detail="result must be one of pending, won, lost, unresolved")

    pick.result = payload.result
    pick.settled_at = None if payload.result == "pending" else utcnow()
    if payload.result == "pending":
        pick.leg_results = None
    _record(db, admin, "admin_pick.result_set", detail={"admin_pick_id": pick_id, "result": payload.result})
    db.commit()

    resolved = _resolve_admin_pick(db, pick)
    if resolved is None:
        # Only reachable if an admin force-reset a finished pick to
        # "pending" and its legs no longer live-resolve -- fall back to the
        # frozen (now-stale) legs rather than erroring the response.
        return admin_pick_to_schema(pick, frozen_admin_pick_legs(db, pick))
    return resolved


@router.patch("/admin-picks/{pick_id}/featured-public", response_model=AdminPickOut)
def set_admin_pick_featured_public(
    pick_id: int,
    payload: FeaturedPublicIn,
    admin: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
) -> AdminPickOut:
    """Commit this pick to the public track record (or take it back out)
    -- only before its first leg kicks off; see app.track_record."""

    pick = db.get(AdminPick, pick_id)
    if pick is None:
        raise HTTPException(status_code=404, detail=f"Admin pick {pick_id} not found")
    try:
        track_record.set_featured(db, pick, payload.featured_public)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _record(
        db, admin, "admin_pick.featured_public_set",
        detail={"admin_pick_id": pick_id, "featured_public": payload.featured_public},
    )
    db.commit()

    resolved = _resolve_admin_pick(db, pick)
    if resolved is None:
        return admin_pick_to_schema(pick, frozen_admin_pick_legs(db, pick))
    return resolved


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


# --- Public teaser pick ----------------------------------------------------
#
# The one combo shown with real selections on the public, unauthenticated
# Welcome page -- see AdminPick.public_teaser / PublicTeaserPickOut for why
# this is a deliberate exception to "selections stay behind the login".


@router.get("/public-teaser-pick", response_model=PublicTeaserPickOut)
def get_public_teaser_pick(db: Session = Depends(get_db)) -> PublicTeaserPickOut:
    """Admin preview of exactly what the public Welcome page is currently
    showing -- same live resolution as the public endpoint itself."""

    return resolve_public_teaser_pick(db)


@router.post("/admin-picks/{pick_id}/promote-to-teaser", response_model=PublicTeaserPickOut)
def promote_pick_to_public_teaser(
    pick_id: int,
    admin: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
) -> PublicTeaserPickOut:
    """Takes an already-generated AdminPick (an AI Generation slip or one
    built by hand) and uses ITS legs as the new public teaser -- the only way
    to set one; there is no random-draw path. The source pick itself is left
    untouched -- this creates a new row, so later editing, settling, or
    deleting the original never touches what's live on the public Welcome
    page, and vice versa.

    Every leg is re-priced live right now, same as resolve_public_teaser_pick
    itself does on every read, and ALL of them must still resolve -- never
    falls back to a weaker combo silently -- so a stale source pick (a
    finished match, a pulled price) is rejected with exactly what's wrong
    rather than quietly going live with fewer legs than the admin saw when
    they chose it."""

    source = db.get(AdminPick, pick_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Pick not found.")

    refs = [(leg["match_id"], leg["market"], leg["selection"]) for leg in source.legs]
    legs, warnings = price_legs(db, refs)
    if len(legs) != len(refs):
        detail = warnings[0] if warnings else "One or more legs in this pick no longer price live."
        raise HTTPException(status_code=400, detail=detail)

    # price_legs only rejects a match once its result has actually landed
    # (status == FINISHED) -- it happily reprices one that has kicked off
    # but is still awaiting a result. expires_at below is the EARLIEST leg
    # kickoff, so a single already-started leg makes the row expired the
    # instant it's created: resolve_public_teaser_pick reads it right back
    # as has_data=False, with no error anywhere to explain why. Reject it
    # here instead, so the admin sees exactly which leg is the problem.
    now = utcnow()
    started = next((leg for leg in legs if leg.kickoff <= now), None)
    if started is not None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{started.home_team} vs {started.away_team} already kicked off "
                f"({started.kickoff:%Y-%m-%d %H:%M} UTC) -- remove or replace that leg "
                "before promoting this pick, or it'll show as empty on the Welcome page."
            ),
        )

    db.execute(delete(AdminPick).where(AdminPick.public_teaser.is_(True)))

    pick = AdminPick(
        legs=[
            {
                "match_id": leg.match_id,
                "market": leg.market,
                "selection": leg.selection,
                "probability_at_pick": leg.model_probability,
                "decimal_odds_at_pick": leg.decimal_odds,
            }
            for leg in legs
        ],
        priced=True,
        label="Public teaser pick",
        created_by_user_id=admin.id,
        public_teaser=True,
        expires_at=min(leg.kickoff for leg in legs),
    )
    db.add(pick)
    _record(
        db, admin, "public_teaser_pick.promoted",
        detail={"source_pick_id": pick_id, "legs": len(legs)},
    )
    db.commit()

    return resolve_public_teaser_pick(db)


@router.post("/my-booking-slips/{slip_id}/promote-to-track-record", response_model=AdminPickOut)
def promote_booking_slip_to_track_record(
    slip_id: int,
    admin: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
) -> AdminPickOut:
    """Commits one of the admin's own booking codes (the "My codes" panel
    on BetCodes) to the public track record as a new AdminPick, which then
    settles on its own like any other. Only before its first match kicks
    off -- publishing a code once its result was known is exactly the
    hindsight selection app.track_record exists to rule out. The original
    BookingSlip is left untouched.

    Scoped to the calling admin's own slips -- "my codes" is personal, and
    this never reaches into another member's booking history."""

    slip = db.get(BookingSlip, slip_id)
    if slip is None or slip.user_id != admin.id:
        raise HTTPException(status_code=404, detail="Booking slip not found.")

    if settle_booking_slip(db, slip):
        db.commit()

    now = utcnow()
    kickoffs = [dt.datetime.fromisoformat(leg["kickoff"]) for leg in slip.legs]
    # The live fixture date wins over the one frozen on the slip, in case
    # the match was rescheduled earlier.
    first = track_record.first_kickoff(db, [leg["match_id"] for leg in slip.legs])
    if slip.result != "pending" or not kickoffs or first is None or min(first, *kickoffs) <= now:
        raise HTTPException(
            status_code=400,
            detail="Only a code whose matches haven't kicked off can be committed to the public track record.",
        )

    label = f"Published from booking code #{slip.id}"
    if db.execute(select(AdminPick.id).where(AdminPick.label == label)).first() is not None:
        # A second copy would count the same call twice in the public record.
        raise HTTPException(status_code=409, detail="This code is already in the public track record.")

    pick = AdminPick(
        legs=[
            {
                "match_id": leg["match_id"],
                "market": leg["market"],
                "selection": leg["selection"],
                "probability_at_pick": leg["model_probability"],
                "decimal_odds_at_pick": leg.get("decimal_odds"),
            }
            for leg in slip.legs
        ],
        result="pending",
        # Never a bookmaker quote on these legs (see book_picks -- each site
        # prices its own slip when opened), same as any other unpriced pick.
        priced=False,
        label=label,
        created_by_user_id=admin.id,
        featured_public=True,
        featured_public_at=now,
        expires_at=max(kickoffs) + dt.timedelta(days=2),
    )
    db.add(pick)
    _record(
        db, admin, "booking_slip.promoted_to_track_record",
        detail={"booking_slip_id": slip_id, "legs": len(pick.legs)},
    )
    db.commit()
    db.refresh(pick)

    resolved = _resolve_admin_pick(db, pick)
    if resolved is None:
        return admin_pick_to_schema(pick, frozen_admin_pick_legs(db, pick))
    return resolved


@router.delete("/public-teaser-pick", status_code=204)
def clear_public_teaser_pick(
    admin: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
) -> None:
    """Removes whatever teaser is currently showing, without replacing it."""

    result = db.execute(delete(AdminPick).where(AdminPick.public_teaser.is_(True)))
    if result.rowcount:
        _record(db, admin, "public_teaser_pick.cleared", detail={"removed": result.rowcount})
        db.commit()
