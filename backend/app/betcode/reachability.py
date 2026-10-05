"""Can this server reach the betting sites booking codes would come from?

Asked before any site connection is built, because the answer can make the
connection pointless: several Nigerian and Ghanaian bookmakers refuse
visitors from outside their country, and this backend runs in Frankfurt. A
connection that works from a laptop in Lagos and fails from the server is
worse than none, because it looks finished.

Each check is one ordinary GET of the site's home page, identified honestly
as this application. Nothing here disguises the request as a browser or
tries to get past a bot challenge -- the result is the same answer a real
connection would get, which is the only answer worth having. A site behind a
challenge is reported as exactly that.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from urllib.parse import urlparse

import requests

# The sites asked for, in both the countries they commonly serve. Checking
# both variants also shows which country's site the server can use.
BETTING_SITES: list[tuple[str, str]] = [
    ("SportyBet Nigeria", "https://www.sportybet.com/ng/"),
    ("SportyBet Ghana", "https://www.sportybet.com/gh/"),
    ("1xBet", "https://1xbet.com/"),
    ("1xBet Nigeria", "https://1xbet.ng/"),
    ("Betway Nigeria", "https://www.betway.com.ng/"),
    ("Betway Ghana", "https://www.betway.com.gh/"),
    ("MSport Ghana", "https://www.msport.com/gh/web"),
]

USER_AGENT = "SoccaIntel-ReachabilityCheck/1.0"
DEFAULT_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class SiteCheck:
    name: str
    url: str
    reachable: bool
    status_code: int | None
    final_url: str | None
    elapsed_ms: int | None
    note: str


def _looks_like_bot_challenge(response: requests.Response) -> bool:
    if response.headers.get("cf-mitigated", "").lower() == "challenge":
        return True
    if response.status_code in (403, 429, 503):
        head = response.text[:4000].lower()
        return any(marker in head for marker in ("just a moment", "captcha", "cf-chl", "attention required"))
    return False


def check_site(name: str, url: str, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> SiteCheck:
    started = time.monotonic()
    try:
        response = requests.get(url, timeout=timeout, allow_redirects=True, headers={"User-Agent": USER_AGENT})
    except requests.RequestException as exc:
        return SiteCheck(
            name=name, url=url, reachable=False, status_code=None, final_url=None, elapsed_ms=None,
            note=f"Could not connect ({type(exc).__name__}). The site may block this server, or be down.",
        )
    elapsed_ms = int((time.monotonic() - started) * 1000)
    status = response.status_code
    final_url = response.url

    def result(reachable: bool, note: str) -> SiteCheck:
        return SiteCheck(
            name=name, url=url, reachable=reachable, status_code=status, final_url=final_url,
            elapsed_ms=elapsed_ms, note=note,
        )

    if _looks_like_bot_challenge(response):
        return result(False, "Behind a bot challenge. A connection can't be built without defeating it, which this project won't do.")
    if status in (403, 451):
        return result(False, f"Refused (HTTP {status}): likely blocks this server's country or automated requests.")
    if status >= 400:
        return result(False, f"Answered with an error (HTTP {status}).")
    if urlparse(final_url).netloc != urlparse(url).netloc:
        return result(True, f"Reachable, but redirected to {final_url} -- check that's the right country's site.")
    return result(True, "Reachable.")


def check_all(timeout: float = DEFAULT_TIMEOUT_SECONDS) -> list[SiteCheck]:
    """Every site in parallel, so the whole check takes about one timeout."""

    with ThreadPoolExecutor(max_workers=len(BETTING_SITES)) as pool:
        return list(pool.map(lambda site: check_site(*site, timeout=timeout), BETTING_SITES))
