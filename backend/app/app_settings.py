"""Superadmin-editable settings, layered over the environment.

Every value here has an environment default and an optional database override.
The environment is what the process starts with; the override is what an
operator changed since, without redeploying to do it.

The registry below is the single source of truth. The API derives its
validation from it, the panel derives its form from it, and adding a setting
means adding one row -- not touching four files and discovering at runtime
that one of them disagreed.

Two rules worth stating because they are easy to get wrong:

**Secrets go in, never out.** A field marked secret can be written and cleared
but is never returned; the API sends a ``*_is_set`` boolean instead. An SMTP
password that round-trips through a browser is an SMTP password in a browser's
memory, its cache, and any extension that asked.

**Unset is not the same as empty.** Deleting an override falls back to the
environment; storing an empty string overrides it with nothing. Both are
useful and they are not the same, so ``reset`` and ``set("")`` are different
operations.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import AppSetting

SettingKind = Literal["str", "int", "float", "bool", "choice"]


@dataclass(frozen=True)
class SettingSpec:
    key: str
    kind: SettingKind
    group: str
    label: str
    help: str = ""
    secret: bool = False
    choices: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    # Name of the Settings attribute this falls back to. Omitted when the
    # setting exists only here and has no environment equivalent.
    env_attr: str | None = None
    default: Any = None

    def coerce(self, raw: str) -> Any:
        if self.kind == "int":
            return int(raw)
        if self.kind == "float":
            return float(raw)
        if self.kind == "bool":
            return raw.strip().lower() in {"1", "true", "yes", "on"}
        return raw


THEME_CHOICES = ("dark", "light")

REGISTRY: tuple[SettingSpec, ...] = (
    # --- Email -----------------------------------------------------------
    SettingSpec("smtp_host", "str", "email", "SMTP host",
                "Leave blank to disable sending. Codes still work; you just send them yourself.",
                env_attr="smtp_host"),
    SettingSpec("smtp_port", "int", "email", "SMTP port",
                "587 for STARTTLS, 465 for implicit TLS.", env_attr="smtp_port", minimum=1, maximum=65535),
    SettingSpec("smtp_user", "str", "email", "SMTP username", env_attr="smtp_user"),
    SettingSpec("smtp_password", "str", "email", "SMTP password",
                "For Gmail this is an app password, not your account password.",
                secret=True, env_attr="smtp_password"),
    SettingSpec("smtp_from", "str", "email", "From address",
                'What the recipient sees, e.g. "Socca Intelligence <you@gmail.com>".',
                env_attr="smtp_from"),
    SettingSpec("smtp_use_tls", "bool", "email", "Use STARTTLS",
                "Ignored on port 465, which is encrypted from the start.", env_attr="smtp_use_tls"),
    SettingSpec("resend_api_key", "str", "email", "Resend API key",
                "Sends over HTTPS instead of SMTP -- set this if your host blocks outbound SMTP "
                "ports (common on free-tier platforms). Takes priority over the SMTP settings "
                "above when set; the From address still comes from there.",
                secret=True, env_attr="resend_api_key"),
    SettingSpec("public_site_url", "str", "email", "Public site URL",
                "Included in the email so the recipient knows where to redeem.",
                env_attr="public_site_url"),

    # --- Data sources ----------------------------------------------------
    SettingSpec("api_football_key", "str", "data", "API-Football key",
                "From api-sports.io. Leave blank to use openfootball alone -- everything "
                "already working keeps working without this.",
                secret=True, env_attr="api_football_key"),
    SettingSpec("api_football_host", "str", "data", "API host",
                "v3.football.api-sports.io direct, or the RapidAPI host if your key is from "
                "there. The auth style follows from this.",
                env_attr="api_football_host"),
    SettingSpec("api_football_daily_budget", "int", "data", "Daily request budget",
                "Pro allows 7,500/day, Free 100. Set lower to leave headroom for manual runs.",
                env_attr="api_football_daily_budget", minimum=1, maximum=1000000),
    SettingSpec("api_football_per_minute", "int", "data", "Requests per minute",
                "Pro allows 300, Free 10. Calls are spaced to stay under this.",
                env_attr="api_football_per_minute", minimum=1, maximum=1000),
    SettingSpec("api_football_capture_odds", "bool", "data", "Capture market odds",
                "Store bookmaker prices so the track record can show whether the model beat "
                "the market, not just whether it was right.",
                default=True),

    # --- Booking codes -----------------------------------------------------
    SettingSpec("betcode_provider", "choice", "betcode", "Booking-code aggregator",
                "None means selections and combined odds still show; only the redeemable "
                "code and deep link need a provider.",
                choices=("none", "mybetcode", "betpaddi"), default="none"),
    SettingSpec("betcode_api_key", "str", "betcode", "Aggregator API key",
                secret=True),
    SettingSpec("betcode_base_url", "str", "betcode", "Aggregator base URL",
                "Leave blank to use the selected provider's default. Only needed to point "
                "at a sandbox/staging host, or if the provider's real domain turns out to "
                "differ from this codebase's unverified guess."),
    SettingSpec("betcode_min_probability", "float", "betcode", "Default accuracy floor",
                "A leg needs at least this model probability to be offered as a candidate.",
                default=0.65, minimum=0.5, maximum=0.99),
    SettingSpec("betcode_max_legs", "int", "betcode", "Default leg cap",
                "How many matches a generated slip may combine.",
                default=8, minimum=1, maximum=15),

    # --- Access ----------------------------------------------------------
    SettingSpec("default_code_duration_days", "int", "access", "Default code duration (days)",
                "Pre-filled when issuing a code. You can still change it per code.",
                default=30, minimum=1, maximum=3650),
    SettingSpec("registration_open", "bool", "access", "Accept new registrations",
                "Turn off to stop new sign-ups. Existing accounts are unaffected.",
                default=True),
    SettingSpec("email_code_by_default", "bool", "access", "Tick “email it to them” by default",
                default=True),
    SettingSpec("trial_enabled", "bool", "access", "Give new signups a free trial",
                "A brand-new account gets full access automatically, no code needed, for the "
                "duration below -- then it reverts to the free tier until a code is redeemed.",
                default=True),
    SettingSpec("trial_duration_days", "int", "access", "Trial length (days)",
                default=5, minimum=1, maximum=30),
    SettingSpec("contact_whatsapp", "str", "access", "WhatsApp contact for access requests",
                "Shown wherever an account needs a Super Admin for a code -- the welcome email, "
                "the locked-access page, and registration. Digits with country code, no spaces "
                "or punctuation (e.g. 233596909643), since this also builds the wa.me link.",
                default="233596909643"),

    # --- Appearance ------------------------------------------------------
    SettingSpec("default_theme", "choice", "appearance", "Default theme",
                "What visitors and new accounts see before choosing their own.",
                choices=THEME_CHOICES, default="dark"),
    SettingSpec("default_accent", "str", "appearance", "Default accent",
                "One of the accent profiles offered in the app.", default="ocean"),
    SettingSpec("site_name", "str", "appearance", "Site name",
                "Shown in the browser tab.", default="Socca Intelligence"),
    SettingSpec("site_tagline", "str", "appearance", "Tagline", default="Football prediction AI"),

    # --- Model -----------------------------------------------------------
    SettingSpec("ensemble_weight_elo", "float", "model", "Elo weight",
                env_attr="ensemble_weight_elo", minimum=0, maximum=1),
    SettingSpec("ensemble_weight_poisson", "float", "model", "Poisson weight",
                env_attr="ensemble_weight_poisson", minimum=0, maximum=1),
    SettingSpec("ensemble_weight_ml", "float", "model", "Gradient boosting weight",
                env_attr="ensemble_weight_ml", minimum=0, maximum=1),
    SettingSpec("home_advantage_elo", "float", "model", "Home advantage (Elo points)",
                env_attr="home_advantage_elo", minimum=0, maximum=300),
    SettingSpec("elo_k_factor", "float", "model", "Elo K-factor",
                "How sharply ratings move after each result.",
                env_attr="elo_k_factor", minimum=1, maximum=100),

    # --- Notice ------------------------------------------------------------
    SettingSpec("notice_enabled", "bool", "notice", "Show a site notice",
                "A banner shown on the Dashboard to every signed-in account -- for a "
                "maintenance window, a new league going live, or anything else worth a "
                "heads-up.",
                default=False),
    SettingSpec("notice_message", "str", "notice", "Notice message",
                "Shown only while the notice above is turned on.", default=""),
)

BY_KEY: dict[str, SettingSpec] = {spec.key: spec for spec in REGISTRY}

GROUP_LABELS = {
    "data": "Data sources",
    "email": "Email",
    "access": "Access & registration",
    "appearance": "Appearance",
    "model": "Model defaults",
    "notice": "Site notice",
    "betcode": "Booking codes",
}


class SettingError(ValueError):
    """A rejected value, with a message meant for the person who typed it."""


def _env_default(spec: SettingSpec) -> Any:
    if spec.env_attr:
        return getattr(get_settings(), spec.env_attr)
    return spec.default


def _overrides(db: Session) -> dict[str, str]:
    return {row.key: row.value for row in db.execute(select(AppSetting)).scalars()}


def get_value(db: Session, key: str) -> Any:
    """One setting: the override if present, otherwise the environment."""

    spec = BY_KEY[key]
    row = db.get(AppSetting, key)
    if row is None:
        return _env_default(spec)
    try:
        return spec.coerce(row.value)
    except (TypeError, ValueError):
        # A stored value that no longer parses -- a type changed, or someone
        # edited the table by hand. Falling back beats raising on every
        # request that touches it.
        return _env_default(spec)


def all_values(db: Session) -> dict[str, Any]:
    overrides = _overrides(db)
    resolved: dict[str, Any] = {}
    for spec in REGISTRY:
        raw = overrides.get(spec.key)
        if raw is None:
            resolved[spec.key] = _env_default(spec)
            continue
        try:
            resolved[spec.key] = spec.coerce(raw)
        except (TypeError, ValueError):
            resolved[spec.key] = _env_default(spec)
    return resolved


def is_overridden(db: Session, key: str) -> bool:
    return db.get(AppSetting, key) is not None


def validate(spec: SettingSpec, value: Any) -> str:
    """Check a value and return how it should be stored."""

    if spec.kind == "bool":
        return "true" if bool(value) else "false"

    if spec.kind in {"int", "float"}:
        try:
            number = int(value) if spec.kind == "int" else float(value)
        except (TypeError, ValueError):
            raise SettingError(f"{spec.label} must be a number.") from None
        if spec.minimum is not None and number < spec.minimum:
            raise SettingError(f"{spec.label} must be at least {spec.minimum:g}.")
        if spec.maximum is not None and number > spec.maximum:
            raise SettingError(f"{spec.label} must be at most {spec.maximum:g}.")
        return str(number)

    text = "" if value is None else str(value).strip()
    if spec.kind == "choice" and text not in spec.choices:
        raise SettingError(f"{spec.label} must be one of: {', '.join(spec.choices)}.")
    return text


def set_values(db: Session, updates: dict[str, Any], *, updated_by_user_id: int | None = None) -> list[str]:
    """Apply several overrides at once. Returns the keys actually changed.

    Validated in full before anything is written, so a form with one bad field
    doesn't half-apply and leave the operator guessing which half landed.
    """

    unknown = set(updates) - set(BY_KEY)
    if unknown:
        raise SettingError(f"Unknown setting(s): {', '.join(sorted(unknown))}.")

    prepared: dict[str, str] = {}
    for key, value in updates.items():
        prepared[key] = validate(BY_KEY[key], value)

    changed: list[str] = []
    now = dt.datetime.utcnow()
    for key, stored in prepared.items():
        row = db.get(AppSetting, key)
        if row is None:
            db.add(AppSetting(key=key, value=stored, updated_at=now, updated_by_user_id=updated_by_user_id))
            changed.append(key)
        elif row.value != stored:
            row.value = stored
            row.updated_at = now
            row.updated_by_user_id = updated_by_user_id
            changed.append(key)
    db.commit()
    return changed


def reset(db: Session, keys: list[str]) -> list[str]:
    """Drop overrides so the environment default applies again."""

    unknown = set(keys) - set(BY_KEY)
    if unknown:
        raise SettingError(f"Unknown setting(s): {', '.join(sorted(unknown))}.")
    existing = [k for k in keys if db.get(AppSetting, k) is not None]
    if existing:
        db.execute(delete(AppSetting).where(AppSetting.key.in_(existing)))
        db.commit()
    return existing
