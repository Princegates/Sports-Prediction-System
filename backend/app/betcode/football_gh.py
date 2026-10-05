"""Football.com Ghana: no separate connection to build.

A SportyBet Ghana booking code, entered as-is on football.com/gh, loads the
same slip there -- confirmed directly against the live site. Rather than
reverse-engineer a second API for a result SportyBet's own connector already
produces, this calls that connector and relabels what it sends back, so a
member who picked "Football.com Ghana" sees that name in the result, not
"SportyBet Ghana".

No link is returned: SportyBetConnector's own link opens on sportybet.com,
not football.com, and this project doesn't yet know football.com's URL for
loading a shared code (if it has one) -- left unset rather than guessed,
the same policy the market tables use for everything else unconfirmed.
"""

from __future__ import annotations

import requests

from app.betcode.selection import Leg
from app.betcode.sites import BookingCodeError, ConnectorResult
from app.betcode.sportybet import SITE_NAME as _SPORTYBET_NAME
from app.betcode.sportybet import SportyBetConnector

SITE_NAME = "Football.com Ghana"


def _relabel(text: str) -> str:
    return text.replace(_SPORTYBET_NAME, SITE_NAME)


class FootballComGhConnector:
    def __init__(self, session: requests.Session | None = None) -> None:
        self._inner = SportyBetConnector(session)

    def create_code(self, legs: list[Leg]) -> ConnectorResult:
        try:
            result = self._inner.create_code(legs)
        except BookingCodeError as exc:
            raise BookingCodeError(_relabel(str(exc))) from exc

        return ConnectorResult(
            code=result.code,
            link=None,
            unavailable_match_ids=list(result.unavailable_match_ids),
            reasons={match_id: _relabel(reason) for match_id, reason in result.reasons.items()},
        )
