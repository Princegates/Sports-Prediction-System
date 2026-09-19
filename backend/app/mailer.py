"""Sending an access code to the person who paid for it.

Two delivery paths. The default is ``smtplib`` from the standard library --
no new dependency, no account with anyone in particular, and the same code
works against a free Gmail app password, a transactional provider's SMTP
bridge, or a self-hosted relay. The only thing that changes is which
environment variables (or panel settings) are filled in.

The second path, a Resend API key, exists because raw SMTP has a real
failure mode this project hit in practice: some hosts -- many free-tier PaaS
platforms among them -- block outbound traffic on the SMTP ports (25, 465,
587 alike) as a blanket anti-abuse measure, regardless of which server you're
trying to reach. No amount of host or port tweaking gets past that, because
the block isn't about the destination. An HTTPS call to Resend's API doesn't
hit that wall, since the app's own traffic already depends on outbound HTTPS
working. When an API key is set, it's used instead of SMTP; the From address
still comes from ``smtp_from`` either way, so switching paths is one field.

Email is optional throughout. A deployment with nothing configured still
issues codes perfectly well; the admin copies the code and sends it however
they already talk to their customers. ``is_configured()`` is what the API
uses to say so honestly instead of failing at send time.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage

import requests
from sqlalchemy.orm import Session

from app.config import get_settings

logger = logging.getLogger(__name__)

RESEND_API_URL = "https://api.resend.com/emails"


class EmailNotConfigured(RuntimeError):
    """Raised when a send is attempted with no SMTP settings present."""


@dataclass
class SendResult:
    sent: bool
    error: str | None = None


@dataclass
class MailConfig:
    host: str
    port: int
    user: str
    password: str
    sender: str
    use_tls: bool
    timeout: float
    site_url: str
    resend_api_key: str


def resolve_config(db: Session | None = None) -> MailConfig:
    """Database overrides first, environment underneath.

    ``db`` is optional so this module stays usable from a script or a context
    with no session; without one it reads the environment alone.
    """

    settings = get_settings()
    values: dict = {}
    if db is not None:
        from app import app_settings

        values = app_settings.all_values(db)

    def pick(key: str, fallback):
        return values.get(key, fallback) if values else fallback

    return MailConfig(
        host=str(pick("smtp_host", settings.smtp_host) or ""),
        port=int(pick("smtp_port", settings.smtp_port) or 587),
        user=str(pick("smtp_user", settings.smtp_user) or ""),
        password=str(pick("smtp_password", settings.smtp_password) or ""),
        sender=str(pick("smtp_from", settings.smtp_from) or ""),
        use_tls=bool(pick("smtp_use_tls", settings.smtp_use_tls)),
        timeout=settings.smtp_timeout,
        site_url=str(pick("public_site_url", settings.public_site_url) or ""),
        resend_api_key=str(pick("resend_api_key", settings.resend_api_key) or ""),
    )


def is_configured(db: Session | None = None) -> bool:
    config = resolve_config(db)
    if config.resend_api_key:
        return bool(config.sender)
    return bool(config.host and config.sender)


def _connect(config: MailConfig) -> smtplib.SMTP | smtplib.SMTP_SSL:
    # Port 465 is implicit TLS (SMTP_SSL); 587 is plaintext upgraded with
    # STARTTLS. Picking the wrong one hangs rather than erroring, which is a
    # miserable thing to debug, so it is derived from the port rather than
    # left as another setting to get wrong.
    if config.port == 465:
        return smtplib.SMTP_SSL(config.host, config.port, timeout=config.timeout,
                                context=ssl.create_default_context())

    client = smtplib.SMTP(config.host, config.port, timeout=config.timeout)
    client.ehlo()
    if config.use_tls:
        client.starttls(context=ssl.create_default_context())
        client.ehlo()
    return client


def _send_via_resend(config: MailConfig, to: str, subject: str, body: str) -> None:
    response = requests.post(
        RESEND_API_URL,
        headers={"Authorization": f"Bearer {config.resend_api_key}"},
        json={"from": config.sender, "to": [to], "subject": subject, "text": body},
        timeout=config.timeout,
    )
    response.raise_for_status()


def _send_via_smtp(config: MailConfig, to: str, subject: str, body: str) -> None:
    message = EmailMessage()
    message["From"] = config.sender
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    client = _connect(config)
    try:
        if config.user:
            client.login(config.user, config.password)
        client.send_message(message)
    finally:
        try:
            client.quit()
        except Exception:  # noqa: BLE001 -- a failed teardown must not mask a successful send
            pass


def send_email(to: str, subject: str, body: str, db: Session | None = None) -> SendResult:
    """Deliver one plain-text message, over Resend's API if a key is set,
    otherwise SMTP.

    Returns a result rather than raising on delivery failure. Every caller so
    far is doing something else that already succeeded -- issuing an access
    code -- and turning "the code exists but the email bounced" into an
    exception would roll back the code and lose it. Reporting the failure
    upward lets the caller keep the code and tell the admin to send it by
    hand.
    """

    config = resolve_config(db)
    configured = bool(config.sender) and (config.resend_api_key or config.host)
    if not configured:
        raise EmailNotConfigured(
            "Email is not configured. Set a Resend API key, or SMTP_HOST and SMTP_FROM (plus "
            "SMTP_USER and SMTP_PASSWORD if your provider requires authentication)."
        )

    try:
        if config.resend_api_key:
            _send_via_resend(config, to, subject, body)
        else:
            _send_via_smtp(config, to, subject, body)
    except Exception as exc:  # noqa: BLE001 -- any delivery failure is reported, never raised
        logger.warning("Failed to send email to %s: %s", to, exc)
        return SendResult(sent=False, error=f"{type(exc).__name__}: {exc}")

    logger.info("Sent %r to %s", subject, to)
    return SendResult(sent=True)


def access_code_message(code: str, duration_days: int, site_url: str | None = None) -> tuple[str, str]:
    """Subject and body for an access-code handover.

    Deliberately plain text. An HTML mail from a new domain with a code in it
    is a shape spam filters know well, and there is nothing here that needs
    formatting.
    """

    days = "1 day" if duration_days == 1 else f"{duration_days} days"
    where = site_url or "the site"
    body = (
        "Here is your access code.\n\n"
        f"    {code}\n\n"
        f"It gives you {days} of access once you redeem it. The clock starts when\n"
        "you redeem it, not now, so there's no rush.\n\n"
        f"To use it: sign in at {where}, and enter the code when prompted.\n"
        "If you don't have an account yet, register first with this email address.\n\n"
        "Predictions are model probabilities, never guarantees of outcome.\n"
    )
    return "Your access code", body


def welcome_message(site_url: str | None = None) -> tuple[str, str]:
    """Subject and body for a new account's confirmation email."""

    where = site_url or "the site"
    body = (
        "Your account has been created.\n\n"
        f"Sign in at {where} whenever you're ready. Registering alone doesn't\n"
        "grant access -- once you've arranged payment with a Super Admin, you'll\n"
        "be issued an access code to redeem.\n\n"
        "If you didn't create this account, you can ignore this email.\n"
    )
    return "Your account is ready", body


def password_changed_message() -> tuple[str, str]:
    """Subject and body for a password-change security notice."""

    body = (
        "Your password was just changed.\n\n"
        "If this was you, there's nothing else to do.\n\n"
        "If it wasn't, someone else may have access to your account -- contact "
        "a Super Admin right away so they can secure it.\n"
    )
    return "Your password was changed", body
