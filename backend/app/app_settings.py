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

from app.timeutil import utcnow
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
    SettingSpec("weekly_picks_email_enabled", "bool", "email", "Email members about new weekly picks",
                "Sent once a week, right after the weekly accumulators are generated, to every "
                "active account that hasn't opted out (Profile -> Email preferences). Off by "
                "default -- turn on only once SMTP/Resend above is actually configured and "
                "tested, since this sends to real members, not just you.",
                default=False),
    SettingSpec("access_reminder_email_enabled", "bool", "email", "Email members with no access yet",
                "Nudges every active account with no live access grant to get a code, run "
                "on demand (scripts/send_access_reminder_email.py / its GitHub Action), not on "
                "a schedule. Off by default -- turn on only once SMTP/Resend above is actually "
                "configured and tested, since this sends to real members, not just you.",
                default=False),
    SettingSpec("pricing_announcement_email_enabled", "bool", "email", "Email members about the price",
                "Announces the current pricing_label (set above) to every active account, not just "
                "ones without access -- for a new price or a change to it. Run on demand "
                "(scripts/send_pricing_announcement_email.py / its GitHub Action), not on a "
                "schedule -- this is a one-time broadcast, not a recurring nudge. Off by default, "
                "same reasoning as every other member-facing email here.",
                default=False),

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
    SettingSpec("betcode_min_probability", "float", "betcode", "Default accuracy floor",
                "A leg needs at least this model probability to be offered as a candidate.",
                default=0.65, minimum=0.5, maximum=0.99),
    SettingSpec("betcode_max_legs", "int", "betcode", "Default leg cap",
                "How many matches a generated slip may combine.",
                default=8, minimum=1, maximum=15),
    SettingSpec("ai_generation_daily_limit", "int", "betcode", "AI Generation calls per user per day",
                "Caps how many times a member can run AI Generation (including the Refresh button) "
                "in a rolling 24 hours -- superadmins are never limited. Set a higher per-user limit "
                "under that member's row on the Users page for someone who's paid for more access.",
                default=50, minimum=1, maximum=100000),

    # --- Access ----------------------------------------------------------
    SettingSpec("pricing_label", "str", "access", "Price shown to visitors",
                "e.g. \"GHC 20 / week\". Shown wherever payment is mentioned -- Welcome, Access, "
                "Register, and the pricing-announcement email (Settings -> Email). Blank keeps the "
                "generic \"arrange payment externally\" wording with no amount, same as before this "
                "existed.",
                default=""),
    SettingSpec("price_amount", "float", "access", "Price amount",
                "With the period below, builds the price shown everywhere -- e.g. 20 and 5 days show as "
                "\"GH₵20 for 5 days\", plus \"about GH₵4 a day\" on the public pages. Leave 0 to use "
                "the free-text price above instead.",
                default=0.0, minimum=0, maximum=100000),
    SettingSpec("price_currency", "str", "access", "Price currency symbol", default="GH₵"),
    SettingSpec("price_days", "int", "access", "Price period (days)",
                "How many days of access the price buys.", default=0, minimum=0, maximum=365),
    SettingSpec("whatsapp_channel_url", "str", "access", "WhatsApp Channel link",
                "Your public WhatsApp Channel (https://whatsapp.com/channel/...). When set, the public "
                "pages invite visitors to follow it for daily picks.",
                default=""),
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
    SettingSpec("referral_enabled", "bool", "access", "Reward referrals",
                "Every account gets its own referral code (Profile -> Refer a friend). When a new "
                "signup enters someone else's code, both accounts get the bonus below added to their "
                "access. Turning this off doesn't remove anyone's code -- it just stops new signups "
                "from earning the bonus by entering one.",
                default=True),
    SettingSpec("referral_bonus_days", "int", "access", "Referral bonus (days, each side)",
                "The new account gets it when its free access starts; the referrer gets it when "
                "that account first redeems a paid code.",
                default=3, minimum=1, maximum=30),
    SettingSpec("referral_monthly_cap", "int", "access", "Referral bonuses per referrer per 30 days",
                "Stops one person farming bonuses from many accounts.",
                default=5, minimum=1, maximum=100),
    SettingSpec("email_verification_required", "bool", "access", "Confirm email before the free trial",
                "New accounts get their trial (and any referral bonus) only after clicking the link "
                "emailed to them. Needs outbound email and the public site URL set; without them, "
                "the trial starts immediately as before.",
                default=True),
    SettingSpec("contact_whatsapp", "str", "access", "WhatsApp contact for access requests",
                "Shown wherever an account needs a Super Admin for a code -- the welcome email, "
                "the locked-access page, and registration. Digits with country code, no spaces "
                "or punctuation (e.g. 233596909643), since this also builds the wa.me link.",
                default="233596909643"),

    # --- Appearance ------------------------------------------------------
    SettingSpec("default_theme", "choice", "appearance", "Theme",
                "Applies to every visitor and account -- there is no per-user override.",
                choices=THEME_CHOICES, default="dark"),
    SettingSpec("default_accent", "str", "appearance", "Accent color",
                "Applies to every visitor and account -- there is no per-user override.", default="sunset"),
    SettingSpec("site_name", "str", "appearance", "Site name",
                "Shown in the browser tab.", default="Socca Intelligence"),
    SettingSpec("site_tagline", "str", "appearance", "Tagline", default="Football prediction AI"),

    # --- Model -----------------------------------------------------------
    SettingSpec("ensemble_weight_elo", "float", "model", "Elo weight",
                "Saving any of the three blend weights replaces the weights the weekly retrain "
                "fits on held-out matches, for every league, from the next prediction on. They "
                "are renormalized, so they need not sum to 1. Reset all three to go back to the "
                "fitted weights -- hand-set weights have measured worse (ROADMAP.md 1e).",
                env_attr="ensemble_weight_elo", minimum=0, maximum=1),
    SettingSpec("ensemble_weight_poisson", "float", "model", "Poisson weight",
                "See Elo weight. An unsaved weight uses the environment default alongside saved ones.",
                env_attr="ensemble_weight_poisson", minimum=0, maximum=1),
    SettingSpec("ensemble_weight_ml", "float", "model", "Gradient boosting weight",
                "See Elo weight. Ignored for a league with no trained model.",
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

    # --- AI assistant rewriter/general chat ---------------------------------
    SettingSpec("assistant_llm_enabled", "bool", "assistant", "Enable the AI extension",
                "Off by default -- Guda's grounded answers are already complete without this. On, "
                "the same LLM does two things: it rewrites a grounded reply for phrasing only (never "
                "shown a fact it wasn't already given), and it answers basic questions the grounded "
                "system can't match to anything (small talk, general football trivia) instead of only "
                "ever handing back a capability menu -- clearly labeled in the UI either way. A failed "
                "or slow call always falls back to the grounded text or menu.",
                env_attr="assistant_llm_enabled"),
    SettingSpec("assistant_llm_base_url", "str", "assistant", "API base URL",
                "Any OpenAI-compatible /chat/completions endpoint. Defaults to Google AI Studio's "
                "Gemini API, which has a genuine free tier (no card needed) -- get a key at "
                "aistudio.google.com/apikey and paste it below.",
                env_attr="assistant_llm_base_url"),
    SettingSpec("assistant_llm_model", "str", "assistant", "Model name",
                "Must match the base URL's provider -- e.g. gemini-3.5-flash-lite for the Gemini "
                "default above (500 free requests/day, vs ~20/day for the flagship Flash model).",
                env_attr="assistant_llm_model"),
    SettingSpec("assistant_llm_api_key", "str", "assistant", "API key",
                "Left blank for a local server (Ollama and similar) that doesn't check one.",
                secret=True, env_attr="assistant_llm_api_key"),
    SettingSpec("assistant_llm_timeout", "float", "assistant", "Request timeout (seconds)",
                env_attr="assistant_llm_timeout", minimum=1, maximum=120),
    SettingSpec("assistant_llm_daily_per_user", "int", "assistant", "AI replies per user per day",
                "After this many AI-phrased or AI-generated replies in 24 hours, a user's answers come "
                "from the built-in pipeline only. Keeps a heavy user from using up a free tier's daily "
                "allowance for everyone, or running up a bill on a paid one.",
                default=30, minimum=1, maximum=1000),

    # --- Guda smart mode (Claude) ---------------------------------------------
    SettingSpec("assistant_agent_enabled", "bool", "assistant", "Smart mode (Claude)",
                "Off by default. On, Guda hands each question to Claude, which works out what is being "
                "asked and looks up exactly the data it needs -- fixtures, predictions, any of the ~100 "
                "markets, form, head-to-head, team news, the model's accuracy, slips -- then answers "
                "from those results only (any percentage it states is checked against them). Handles "
                "typos, slang and follow-ups, and compound questions the built-in pipeline can't. If "
                "Claude is slow, unreachable or over its limits, Guda answers from the built-in "
                "pipeline instead, so it never goes silent. Bills your Anthropic account per question.",
                env_attr="assistant_agent_enabled"),
    SettingSpec("assistant_agent_api_key", "str", "assistant", "Anthropic API key",
                "From console.anthropic.com -> API keys. A separate account from claude.ai chat "
                "subscriptions; usage is billed there.",
                secret=True, env_attr="assistant_agent_api_key"),
    SettingSpec("assistant_agent_model", "str", "assistant", "Claude model",
                "claude-haiku-5-5 (the default) is fast and a fraction of a cent a question, with less depth "
                "on hard, multi-step questions; claude-sonnet-5-5 is stronger, roughly 3 to 6 US cents; "
                "claude-opus-5-5 is the most capable, roughly 5 to 12 US cents (a question that looks "
                "several things up resends its context at each step). Estimates -- watch your Anthropic "
                "usage page. The daily limits below cap the spend. Type the name exactly: a wrong name "
                "makes every smart answer fail and Guda falls back to its built-in pipeline.",
                env_attr="assistant_agent_model"),
    SettingSpec("assistant_agent_effort", "str", "assistant", "Thinking effort",
                "How hard Claude thinks before answering: low is quickest and cheapest, high is the "
                "most thorough. Medium suits most questions.",
                choices=("low", "medium", "high"), env_attr="assistant_agent_effort"),
    SettingSpec("assistant_agent_daily_per_user", "int", "assistant", "Smart-mode questions per user per day",
                "After this many in 24 hours a user's questions are answered by the built-in pipeline "
                "instead. Smart mode costs real money per question, so keep this modest.",
                default=10, minimum=1, maximum=1000),
    SettingSpec("assistant_agent_daily_total", "int", "assistant", "Smart-mode questions per day, whole site",
                "A ceiling on the day's bill: once this many smart-mode answers have been given in 24 "
                "hours, everyone falls back to the built-in pipeline until the count drops. With "
                "Haiku, 300 is well under a dollar a day; on Opus at roughly 10 cents a question it is about $30.",
                default=300, minimum=1, maximum=100000),
    SettingSpec("assistant_agent_timeout", "float", "assistant", "Smart-mode time limit (seconds)",
                "Longest a single Claude call may take before Guda falls back to the built-in answer.",
                env_attr="assistant_agent_timeout", minimum=10, maximum=170),

    # --- Guda Picks ----------------------------------------------------------
    SettingSpec("guda_picks_enabled", "bool", "picks", "Show Guda Picks",
                "A dashboard section for outcomes a Super Admin has chosen to highlight -- "
                "always a real, currently-live model probability, never a frozen number or "
                "a freely written claim.",
                default=True),
    SettingSpec("guda_picks_free_tier_visible", "bool", "picks", "Show it to the free tier too",
                "Off restricts Guda Picks to accounts with redeemed access -- on shows it to "
                "everyone, same as the rest of the Dashboard.",
                default=True),

    # --- Admin Picks -----------------------------------------------------
    SettingSpec("admin_picks_enabled", "bool", "picks", "Show Admin Picks",
                "A dashboard section for whole AI Generation slips a Super Admin has chosen "
                "to highlight -- combined odds, combined probability and risk tier are all "
                "recomputed live, never a frozen number.",
                default=True),
    SettingSpec("admin_picks_free_tier_visible", "bool", "picks", "Show it to the free tier too",
                "Off restricts Admin Picks to accounts with redeemed access -- on shows it to "
                "everyone, same as the rest of the Dashboard.",
                default=True),

    # --- Betting markets page ---------------------------------------------
    SettingSpec("default_market_tab", "choice", "markets", "Default markets",
                "Which markets the Betting Markets page's dropdown starts with for every "
                "visitor, until they choose their own.",
                choices=("match_result", "double_chance", "btts", "draw_no_bet", "other"),
                default="match_result"),
)

BY_KEY: dict[str, SettingSpec] = {spec.key: spec for spec in REGISTRY}

GROUP_LABELS = {
    "data": "Data sources",
    "email": "Email",
    "access": "Access & registration",
    "appearance": "Appearance",
    "model": "Model defaults",
    "notice": "Site notice",
    "picks": "Guda Picks",
    "betcode": "Booking codes",
    "assistant": "AI assistant",
    "markets": "Betting markets",
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


def _format_amount(amount: float) -> str:
    return f"{amount:,.0f}" if float(amount).is_integer() else f"{amount:,.2f}"


def price_display(values: dict[str, Any]) -> tuple[str, str]:
    """``(label, per_day)`` for the price, from ``all_values`` output.

    The structured fields (amount, currency, days) win when both amount and
    days are set -- "GH₵20 for 5 days" and "about GH₵4 a day" -- so the
    label can't carry a typo and the per-day figure is always right.
    Otherwise the free-text ``pricing_label`` as typed, with no per-day.
    Both are "" when no price is configured.
    """

    amount = float(values.get("price_amount") or 0)
    days = int(values.get("price_days") or 0)
    if amount > 0 and days > 0:
        currency = str(values.get("price_currency") or "")
        label = f"{currency}{_format_amount(amount)} for {days} day{'s' if days != 1 else ''}"
        per_day = f"about {currency}{_format_amount(round(amount / days, 2))} a day" if days > 1 else ""
        return label, per_day
    return str(values.get("pricing_label") or "").strip(), ""


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


def overridden_values(db: Session, keys: list[str]) -> dict[str, Any]:
    """The saved overrides among ``keys``, coerced -- one query, for callers
    that need to know which settings the panel actually set rather than
    what they resolve to. A stored value that no longer parses is left out,
    as ``get_value`` would ignore it."""

    rows = db.execute(select(AppSetting).where(AppSetting.key.in_(keys))).scalars()
    values: dict[str, Any] = {}
    for row in rows:
        try:
            values[row.key] = BY_KEY[row.key].coerce(row.value)
        except (KeyError, TypeError, ValueError):
            continue
    return values


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
    now = utcnow()
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
