"""Telling one inbox from another, for the one free trial per person.

``canonical_email`` folds the spellings that reach the same inbox: the part
after "+" is dropped for every provider (plus-addressing is near universal),
and for Gmail the dots are dropped and googlemail.com is gmail.com. Accounts
whose canonical address matches can't both register.

``is_disposable`` refuses the throwaway-inbox services that make a fresh
trial a few seconds' work. The list is short on purpose -- the services that
matter -- and is a speed bump, not a wall; paid access is unaffected.
"""

from __future__ import annotations

_GMAIL = {"gmail.com", "googlemail.com"}

DISPOSABLE_DOMAINS = frozenset({
    "10minutemail.com", "10minutemail.net", "20minutemail.com", "33mail.com", "anonaddy.me",
    "burnermail.io", "byom.de", "deadaddress.com", "discard.email", "dispostable.com",
    "dropmail.me", "emailondeck.com", "fakeinbox.com", "fakemail.net", "getairmail.com",
    "getnada.com", "guerrillamail.biz", "guerrillamail.com", "guerrillamail.de", "guerrillamail.info",
    "guerrillamail.net", "guerrillamail.org", "guerrillamailblock.com", "harakirimail.com",
    "inboxbear.com", "incognitomail.org", "jetable.org", "mailcatch.com", "maildrop.cc",
    "mailinator.com", "mailinator.net", "mailnesia.com", "mailpoof.com", "mailsac.com",
    "mailtemp.net", "mintemail.com", "mohmal.com", "moakt.com", "mytemp.email", "nada.email",
    "sharklasers.com", "spam4.me", "spamgourmet.com", "temp-mail.io", "temp-mail.org",
    "tempail.com", "tempinbox.com", "tempmail.com", "tempmail.dev", "tempmail.net",
    "tempmailo.com", "tempr.email", "throwawaymail.com", "trashmail.com", "trashmail.de",
    "trashmail.net", "yopmail.com", "yopmail.fr", "yopmail.net", "emailfake.com",
    "1secmail.com", "1secmail.net", "1secmail.org", "linshiyouxiang.net", "grr.la",
    "pokemail.net", "spambox.us", "mail.tm", "minuteinbox.com", "tmail.ws", "tmpmail.org",
})


def _split(email: str) -> tuple[str, str]:
    local, _, domain = (email or "").strip().lower().rpartition("@")
    return local, domain


def canonical_email(email: str) -> str:
    local, domain = _split(email)
    if not local:
        return (email or "").strip().lower()
    local = local.split("+", 1)[0]
    if domain in _GMAIL:
        local = local.replace(".", "")
        domain = "gmail.com"
    return f"{local}@{domain}"


def is_disposable(email: str) -> bool:
    _, domain = _split(email)
    return any(domain == d or domain.endswith("." + d) for d in DISPOSABLE_DOMAINS)
