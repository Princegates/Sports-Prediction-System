"""The boundary to a booking-code aggregator -- the "spins up the virtual
slip and returns a string" step.

Nothing on this side of the boundary is guessed. ``NotConfiguredProvider`` is
the default, and it does exactly one thing: says plainly that no aggregator
is set up, and returns no code. It would be easy to fabricate a
plausible-looking string here instead -- the request in this codebase's own
history literally used ``BC34XYZ`` as an example -- and that is precisely
what must never happen. A fake code is indistinguishable from a real one
until someone pastes it into a betting app and it fails, at which point the
platform that generated it looks broken or dishonest. "No code yet" is a
worse-looking response and a true one.

Two real HTTP clients exist below, ``MyBetCodeProvider`` and
``BetPaddiProvider``, both built on ``_HttpBookingCodeProvider``. Their
endpoint path, auth header and request/response field names are this
module's one placeholder, shared by both because neither has been checked
against real documentation -- this sandbox cannot reach either site. They
follow the shape most booking-code aggregators use (an API key header, a
JSON body of legs described by bookmaker/competition/selection text, a
response carrying the code and a deep link), which is a reasonable default
and not a confirmed one. Every line that needs checking once real docs are
in hand is marked ``CONFIRM``, in one place so fixing it for one aggregator
doesn't mean re-deriving the same fix for the other.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.orm import Session

from app import app_settings
from app.betcode.selection import Leg


class BookingCodeError(Exception):
    """The provider step failed for a reason worth showing verbatim."""


class ProviderNotConfigured(BookingCodeError):
    """No aggregator is set up. Not a failure -- the honest default state."""


@dataclass(frozen=True)
class ProviderResult:
    code: str
    deep_link: str | None
    raw: dict | None = None


class BookingCodeProvider(Protocol):
    def create_slip(self, *, bookmaker: str, legs: list[Leg]) -> ProviderResult: ...


class NotConfiguredProvider:
    """The default. Explains why there is no code rather than inventing one."""

    def create_slip(self, *, bookmaker: str, legs: list[Leg]) -> ProviderResult:
        raise ProviderNotConfigured(
            "No booking-code aggregator is configured. Add a provider and API key in "
            "Settings -> Booking codes to generate real bookmaker codes; until then, the "
            "selections and combined price above are still real -- just not turned into "
            "a redeemable code."
        )


class _HttpBookingCodeProvider:
    """Shared shape for a real aggregator client -- see the module docstring
    for what is and isn't verified here. Subclasses give only a display name
    (for error messages) and a default base URL.
    """

    display_name = "the aggregator"
    default_base_url = "https://example.invalid"  # every real subclass overrides this

    def __init__(self, *, api_key: str, base_url: str, timeout: float = 15.0) -> None:
        if not api_key:
            raise ProviderNotConfigured(f"A {self.display_name} API key is set as the provider but is empty.")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def create_slip(self, *, bookmaker: str, legs: list[Leg]) -> ProviderResult:
        if not legs:
            raise BookingCodeError("Cannot generate a code for an empty slip.")

        # CONFIRM: endpoint path, once real docs are available.
        url = f"{self._base_url}/v1/booking-codes"

        # CONFIRM: whether the aggregator wants each leg addressed by
        # bookmaker-specific fixture/market/selection IDs (which would mean
        # an earlier resolve-by-name call this client does not yet make) or,
        # as assumed here, descriptive text it resolves on its own side --
        # which is what the product description this was built from implies
        # ("maps your selections to the specific bookmaker requested").
        payload = {
            "bookmaker": bookmaker,
            "selections": [
                {
                    "home_team": leg.home_team,
                    "away_team": leg.away_team,
                    "league": leg.league,
                    "kickoff": leg.kickoff.isoformat(),
                    "market": leg.market,
                    "selection": leg.selection,
                    "odds": leg.decimal_odds,
                }
                for leg in legs
            ],
        }

        # CONFIRM: header name and scheme (Bearer vs a custom header, e.g.
        # x-api-key -- both are common and this guesses Bearer).
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                body = json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:300].decode(errors="replace")
            raise BookingCodeError(
                f"{self.display_name} rejected the request (HTTP {exc.code}): {detail}"
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise BookingCodeError(f"Could not reach {self.display_name}: {type(exc).__name__}: {exc}") from exc

        # CONFIRM: response field names.
        code = body.get("code") or body.get("booking_code")
        if not code:
            raise BookingCodeError(f"{self.display_name} did not return a code: {body!r}")

        return ProviderResult(code=str(code), deep_link=body.get("deep_link"), raw=body)


class MyBetCodeProvider(_HttpBookingCodeProvider):
    display_name = "MyBetCode"
    default_base_url = "https://api.mybetcode.com"


class BetPaddiProvider(_HttpBookingCodeProvider):
    display_name = "BetPaddi"
    default_base_url = "https://api.betpaddi.com"


# One entry per real provider: the settings-panel value it's chosen by, and
# the class that implements it. Adding a third aggregator is one line here,
# not a new branch buried in get_provider.
_PROVIDERS: dict[str, type[_HttpBookingCodeProvider]] = {
    "mybetcode": MyBetCodeProvider,
    "betpaddi": BetPaddiProvider,
}


def get_provider(db: Session) -> BookingCodeProvider:
    """Which provider is configured, read from the settings panel."""

    values = app_settings.all_values(db)
    provider_key = str(values.get("betcode_provider") or "none")

    provider_cls = _PROVIDERS.get(provider_key)
    if provider_cls is None:
        return NotConfiguredProvider()

    return provider_cls(
        api_key=str(values.get("betcode_api_key") or ""),
        base_url=str(values.get("betcode_base_url") or provider_cls.default_base_url),
    )
