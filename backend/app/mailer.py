"""Sending an access code to the person who paid for it.

Uses ``smtplib`` from the standard library rather than a provider SDK. Not
purism -- it means no new dependency, no account with anyone in particular,
and the same code works against a free Gmail app password, a transactional
provider's SMTP bridge, or a self-hosted relay. The only thing that changes
is five environment variables.

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

from app.config import get_settings

logger = logging.getLogger(__name__)


class EmailNotConfigured(RuntimeError):
    """Raised when a send is attempted with no SMTP settings present."""


@dataclass
class SendResult:
    sent: bool
    error: str | None = None


def is_configured() -> bool:
    settings = get_settings()
    return bool(settings.smtp_host and settings.smtp_from)


def _connect(settings) -> smtplib.SMTP | smtplib.SMTP_SSL:
    # Port 465 is implicit TLS (SMTP_SSL); 587 is plaintext upgraded with
    # STARTTLS. Picking the wrong one hangs rather than erroring, which is a
    # miserable thing to debug, so it is derived from the port rather than
    # left as another setting to get wrong.
    if settings.smtp_port == 465:
        return smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout,
                                context=ssl.create_default_context())

    client = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout)
    client.ehlo()
    if settings.smtp_use_tls:
        client.starttls(context=ssl.create_default_context())
        client.ehlo()
    return client


def send_email(to: str, subject: str, body: str) -> SendResult:
    """Deliver one plain-text message.

    Returns a result rather than raising on delivery failure. Every caller so
    far is doing something else that already succeeded -- issuing an access
    code -- and turning "the code exists but the email bounced" into an
    exception would roll back the code and lose it. Reporting the failure
    upward lets the caller keep the code and tell the admin to send it by
    hand.
    """

    settings = get_settings()
    if not is_configured():
        raise EmailNotConfigured(
            "SMTP is not configured. Set SMTP_HOST and SMTP_FROM (plus SMTP_USER "
            "and SMTP_PASSWORD if your provider requires authentication)."
        )

    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    try:
        client = _connect(settings)
        try:
            if settings.smtp_user:
                client.login(settings.smtp_user, settings.smtp_password)
            client.send_message(message)
        finally:
            try:
                client.quit()
            except Exception:  # noqa: BLE001 -- a failed teardown must not mask a successful send
                pass
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
