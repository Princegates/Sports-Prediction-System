"""Superadmin settings and system status.

The settings half is a thin shell over ``app.app_settings`` -- the registry
there decides what exists, what type it is and how it validates, so this file
stays a transport layer rather than a second place to keep those rules.

The status half exists because of a specific, expensive experience: an outage
where the site showed empty tiles and the cause could have been any of the
database, the API, the models or CORS, and finding out meant reading logs on
three different dashboards. One page that answers "which layer is broken" is
worth more than it costs.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import app_settings, mailer
from app.api.deps import get_db, require_superadmin
from app.api.schemas import (
    SettingSpecOut,
    SettingsOut,
    SettingsUpdateIn,
    SystemStatusOut,
    TestEmailIn,
    TestEmailOut,
)
from app.db.models import AccessCode, AccessGrant, AppSetting, Match, Prediction, User
from app.model_store import MODEL_DIR

router = APIRouter(prefix="/api/admin", tags=["settings"], dependencies=[Depends(require_superadmin)])

# Stands in for a secret that is set. Not the real length -- that leaks how
# long the password is.
SECRET_PLACEHOLDER = "••••••••"


def _spec_out(spec: app_settings.SettingSpec) -> SettingSpecOut:
    return SettingSpecOut(
        key=spec.key,
        kind=spec.kind,
        group=spec.group,
        label=spec.label,
        help=spec.help,
        secret=spec.secret,
        choices=list(spec.choices),
        minimum=spec.minimum,
        maximum=spec.maximum,
    )


@router.get("/settings", response_model=SettingsOut)
def read_settings(db: Session = Depends(get_db)) -> SettingsOut:
    values = app_settings.all_values(db)
    overridden = {row.key for row in db.execute(select(AppSetting)).scalars()}

    safe: dict[str, object] = {}
    secrets_set: dict[str, bool] = {}
    for spec in app_settings.REGISTRY:
        if spec.secret:
            # Written and cleared, never read back.
            secrets_set[spec.key] = bool(values.get(spec.key))
            safe[spec.key] = SECRET_PLACEHOLDER if values.get(spec.key) else ""
            continue
        safe[spec.key] = values[spec.key]

    return SettingsOut(
        values=safe,
        secrets_set=secrets_set,
        overridden=sorted(overridden),
        groups=app_settings.GROUP_LABELS,
        specs=[_spec_out(s) for s in app_settings.REGISTRY],
    )


@router.patch("/settings", response_model=SettingsOut)
def update_settings(
    payload: SettingsUpdateIn,
    admin: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
) -> SettingsOut:
    updates = dict(payload.values or {})

    # The placeholder coming back unchanged means "leave it alone", not "set
    # the password to eight bullets" -- which is what would happen if the form
    # posted what it was shown.
    for spec in app_settings.REGISTRY:
        if spec.secret and updates.get(spec.key) == SECRET_PLACEHOLDER:
            updates.pop(spec.key)

    try:
        if payload.reset:
            app_settings.reset(db, payload.reset)
        if updates:
            app_settings.set_values(db, updates, updated_by_user_id=admin.id)
    except app_settings.SettingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return read_settings(db)


@router.post("/settings/test-email", response_model=TestEmailOut)
def send_test_email(
    payload: TestEmailIn,
    admin: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
) -> TestEmailOut:
    """Prove the mail settings work before a real code depends on them.

    Sends to the admin's own address unless told otherwise, so a
    misconfiguration can't spray a stranger's inbox while being debugged.
    """

    to = (payload.to or admin.email).strip()
    if not mailer.is_configured(db):
        return TestEmailOut(
            sent=False,
            detail="Email is not configured. Set at least an SMTP host and a from-address.",
        )

    result = mailer.send_email(
        to,
        "Test email from your prediction platform",
        "If you are reading this, your SMTP settings work.\n\n"
        "Access codes will be delivered using these settings.\n",
        db=db,
    )
    return TestEmailOut(
        sent=result.sent,
        detail=f"Sent to {to}." if result.sent else (result.error or "Unknown error."),
    )


@router.get("/system-status", response_model=SystemStatusOut)
def system_status(db: Session = Depends(get_db)) -> SystemStatusOut:
    """Which layer is broken, on one page."""

    latest_match = db.execute(select(func.max(Match.date))).scalar()
    latest_prediction = db.execute(select(func.max(Prediction.created_at))).scalar()
    now = dt.datetime.utcnow()

    models = sorted(p.name for p in MODEL_DIR.glob("ml_model_*.joblib")) if MODEL_DIR.exists() else []
    calibrators = len(list(MODEL_DIR.glob("calibrator_*.joblib"))) if MODEL_DIR.exists() else 0
    newest_model = None
    if models:
        newest_model = dt.datetime.utcfromtimestamp(
            max((MODEL_DIR / name).stat().st_mtime for name in models)
        )

    return SystemStatusOut(
        database_reachable=True,  # reaching this line required a query
        matches=db.execute(select(func.count()).select_from(Match)).scalar() or 0,
        predictions=db.execute(select(func.count()).select_from(Prediction)).scalar() or 0,
        upcoming_fixtures=db.execute(
            select(func.count()).select_from(Match).where(Match.date >= now, Match.status == "SCHEDULED")
        ).scalar() or 0,
        leagues=sorted({r for r in db.execute(select(Match.league).distinct()).scalars() if r}),
        users=db.execute(select(func.count()).select_from(User)).scalar() or 0,
        active_grants=db.execute(
            select(func.count()).select_from(AccessGrant).where(
                AccessGrant.status == "active", AccessGrant.expires_at > now
            )
        ).scalar() or 0,
        unredeemed_codes=db.execute(
            select(func.count()).select_from(AccessCode).where(
                AccessCode.status == "active", AccessCode.redemption_count == 0
            )
        ).scalar() or 0,
        latest_match_date=latest_match,
        latest_prediction_at=latest_prediction,
        model_files=models,
        calibrator_files=calibrators,
        models_built_at=newest_model,
        email_configured=mailer.is_configured(db),
        settings_overridden=db.execute(select(func.count()).select_from(AppSetting)).scalar() or 0,
    )
