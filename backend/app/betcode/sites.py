"""Betting sites a slip can be turned into booking codes for.

One slip, several sites: each site gets the same legs and answers on its own
-- a code, a code with some legs left out (a match or market it doesn't
offer), or no code and the reason. A site with no connection built yet says
so; nothing here ever produces a code a site didn't issue itself.

A connection is a class with one method, ``create_code(legs)``, that finds
each leg on the site, books the slip there and returns what the site sent
back. Registering one is a line in ``SITES``; until then the site is listed
as not connected and the booking step stays hidden from members.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol

from app.betcode.providers import BookingCodeError
from app.betcode.selection import Leg


@dataclass(frozen=True)
class ConnectorResult:
    code: str
    link: str | None
    # match_ids of legs the site couldn't include -- the code covers the rest.
    unavailable_match_ids: list[int] = field(default_factory=list)


class SiteConnector(Protocol):
    def create_code(self, legs: list[Leg]) -> ConnectorResult: ...


@dataclass(frozen=True)
class Site:
    key: str
    name: str
    # None until this site's connection is built.
    connector: Callable[[], SiteConnector] | None = None

    @property
    def connected(self) -> bool:
        return self.connector is not None


def _sportybet_gh() -> SiteConnector:
    # Imported here: the connector module imports ConnectorResult from this one.
    from app.betcode.sportybet import SportyBetConnector

    return SportyBetConnector()


SITES: list[Site] = [
    Site("sportybet_gh", "SportyBet Ghana", _sportybet_gh),
    Site("betway_gh", "Betway Ghana"),
    Site("1xbet", "1xBet"),
]

SITES_BY_KEY = {site.key: site for site in SITES}


@dataclass(frozen=True)
class SiteCode:
    site: str
    name: str
    # code_ready | not_connected | error
    status: str
    code: str | None = None
    link: str | None = None
    message: str | None = None
    unavailable_match_ids: list[int] = field(default_factory=list)

    def as_json(self) -> dict:
        return {
            "site": self.site, "name": self.name, "status": self.status, "code": self.code,
            "link": self.link, "message": self.message, "unavailable_match_ids": list(self.unavailable_match_ids),
        }


def code_for_site(site_key: str, legs: list[Leg]) -> SiteCode:
    site = SITES_BY_KEY.get(site_key)
    if site is None:
        return SiteCode(site=site_key, name=site_key, status="error", message=f"Unknown betting site {site_key!r}.")
    if site.connector is None:
        return SiteCode(
            site=site.key, name=site.name, status="not_connected",
            message=f"{site.name} isn't connected yet, so no code can be made for it.",
        )

    try:
        result = site.connector().create_code(legs)
    except BookingCodeError as exc:
        return SiteCode(site=site.key, name=site.name, status="error", message=str(exc))

    dropped = len(result.unavailable_match_ids)
    message = None
    if dropped:
        message = (
            f"{site.name} doesn't offer {dropped} of these {len(legs)} picks, so the code covers the "
            f"other {len(legs) - dropped}."
        )
    return SiteCode(
        site=site.key, name=site.name, status="code_ready", code=result.code, link=result.link,
        message=message, unavailable_match_ids=list(result.unavailable_match_ids),
    )


def codes_for_sites(site_keys: list[str], legs: list[Leg]) -> list[SiteCode]:
    """One result per requested site, in the order asked, duplicates dropped."""

    seen: set[str] = set()
    ordered = [k for k in site_keys if not (k in seen or seen.add(k))]
    return [code_for_site(key, legs) for key in ordered]
