"""MSport Ghana: find each leg on the site and book the slip there.

Two steps, each against an endpoint MSport's own web page uses and that
answers without an account -- confirmed by capturing a live booking request:
its cookies carried only device/analytics ids, no session or auth token.

1. **Find the match.** A leg is looked up in MSport's one fixtures list
   (``facts-center/query/frontend/sports-matches-list``), which groups
   matches by tournament. A leg is the event on the same date (a day either
   way) whose home *and* away teams both match ours by name -- the same
   two-sided matching ``sportybet.py`` uses, and for the same reason: one
   side alone is not evidence an event is the right one.
2. **Translate the pick, then book it.** MSport runs on the same
   Sportradar-backed platform as SportyBet -- confirmed by capturing a
   live match's full market list and finding its Sportradar market and
   outcome ids identical to SportyBet's own, for every market checked but
   one (see ``_MARKETS`` below). So the translation table is SportyBet's own,
   reused rather than re-derived. ``orders/real-sports/order/share`` takes
   the translated selections and returns the booking code.

Requests identify themselves as this application and carry only the headers
the API itself reads (client id, operator id, platform). Nothing here poses
as a browser or works around a refusal: if MSport turns this server away,
the member is told so, which is the answer a real connection gets.
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
import time
from dataclasses import dataclass

import requests

from app.betcode.selection import Leg
from app.betcode.sites import BookingCodeError, ConnectorResult
from app.betcode.sportybet import _MARKETS as _SPORTYBET_MARKETS
from app.data.team_matching import name_match_score

logger = logging.getLogger(__name__)

SITE_NAME = "MSport Ghana"
BASE_URL = "https://www.msport.com"
COUNTRY = "gh"
EVENTS_URL = f"{BASE_URL}/api/{COUNTRY}/facts-center/query/frontend/sports-matches-list"
SHARE_URL = f"{BASE_URL}/api/{COUNTRY}/orders/real-sports/order/share"

FOOTBALL = "sr:sport:1"
PAGE_SIZE = 100
# The fixtures list's reply carries a ``lastEventId`` that looks like a
# paging cursor, but the request shape that would consume it was never
# captured, so only the one call is made. That single call already returns
# several leagues and rounds -- a leg it doesn't carry is simply not found,
# same as SportyBet's own cross-league list once paging is exhausted.
EVENTS_CACHE_SECONDS = 300
TIMEOUT_SECONDS = 10.0

USER_AGENT = "SoccaIntel/1.0 (booking codes)"
_API_HEADERS = {
    "Accept": "application/json",
    "User-Agent": USER_AGENT,
    "clientid": "WEB",
    "operid": "3",
    "platform": "WEB",
}
_OK = 10000

KICKOFF_TOLERANCE = dt.timedelta(days=1)
NAME_THRESHOLD = 0.82


# (our market, our selection) -> (marketId, specifier, outcomeId).
#
# SportyBet's own table (sportybet._build_markets), reused rather than
# duplicated: a live MSport match's full 234-market list was captured and
# its Sportradar market/outcome ids matched SportyBet's table exactly for
# every market checked -- 1X2, Over/Under, Double Chance, BTTS, Draw No Bet,
# Odd/Even, Correct Score, HT/FT, Winning Margin among them. The one checked
# exception is "Total Goals Range": MSport reads it under Sportradar variant
# ``sr:exact_goals:5+`` where SportyBet's table uses ``sr:exact_goals:6+``,
# and MSport's own outcome ids for that variant were never read off its
# page -- left out rather than guessed, same as SportyBet's own table leaves
# out bets it has no match for.
_MARKETS = {k: v for k, v in _SPORTYBET_MARKETS.items() if k[0] != "Total Goals Range"}


def translate(market: str, selection: str) -> tuple[str, str | None, str] | None:
    """MSport's (marketId, specifier, outcomeId) for one of our picks, or
    None when MSport has no bet that settles the same way (or the mapping
    isn't confirmed for it yet)."""

    return _MARKETS.get((market, selection))


# --- the event list ----------------------------------------------------------


@dataclass(frozen=True)
class Event:
    event_id: str
    home: str
    away: str
    kickoff: dt.datetime  # naive UTC, like Match.date


def _parse_events(payload: dict) -> list[Event]:
    if payload.get("bizCode") != _OK:
        raise BookingCodeError(f"{SITE_NAME} wouldn't list its matches: {payload.get('message') or 'no reason given'}.")
    data = payload.get("data") or {}
    raw_events = list(data.get("events") or [])
    for tournament in data.get("tournaments") or []:
        raw_events.extend(tournament.get("events") or [])

    events = []
    for raw in raw_events:
        try:
            events.append(Event(
                event_id=str(raw["eventId"]),
                home=str(raw["homeTeam"]),
                away=str(raw["awayTeam"]),
                kickoff=dt.datetime.fromtimestamp(int(raw["startTime"]) / 1000, dt.timezone.utc).replace(tzinfo=None),
            ))
        except (KeyError, TypeError, ValueError):
            continue
    return events


class _EventList:
    """Upcoming events, fetched from the one fixtures list and reused for a
    few minutes -- the list barely changes minute to minute, and a member
    often books the same slip twice (another site, a tweaked leg)."""

    def __init__(self, session: requests.Session) -> None:
        self._session = session
        self._lock = threading.Lock()
        self.events: list[Event] = []
        self._fetched_at: float | None = None

    def _fetch(self) -> list[Event]:
        try:
            response = self._session.get(
                EVENTS_URL, params={"sportId": FOOTBALL}, headers=_API_HEADERS, timeout=TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise BookingCodeError(f"Couldn't reach {SITE_NAME} from this server ({type(exc).__name__}).") from exc
        if response.status_code != 200:
            raise BookingCodeError(f"{SITE_NAME} refused the match list (HTTP {response.status_code}).")
        try:
            return _parse_events(response.json())
        except ValueError as exc:
            raise BookingCodeError(f"{SITE_NAME} sent a match list this connection can't read.") from exc

    def find_all(self, legs: list[Leg]) -> dict[int, str]:
        with self._lock:
            if self._fetched_at is None or time.monotonic() - self._fetched_at > EVENTS_CACHE_SECONDS:
                self.events = self._fetch()
                self._fetched_at = time.monotonic()
            return _match_legs(legs, self.events)


def _match_legs(legs: list[Leg], events: list[Event]) -> dict[int, str]:
    found: dict[int, str] = {}
    for leg in legs:
        event = find_event(leg, events)
        if event is not None:
            found[leg.match_id] = event.event_id
    return found


def find_event(leg: Leg, events: list[Event]) -> Event | None:
    """The one event that is this leg's fixture, or None.

    Both teams must clear the threshold on their own, in order. Among those,
    better-covered names win and then the nearer kickoff; an exact tie
    between two different events is refused, because picking one would book
    a bet on a match the member didn't choose. Mirrors sportybet.find_event,
    which this was modelled on.
    """

    kickoff = leg.kickoff
    if kickoff.tzinfo is not None:
        kickoff = kickoff.astimezone(dt.timezone.utc).replace(tzinfo=None)

    ranked = []
    for event in events:
        if abs(event.kickoff - kickoff) > KICKOFF_TOLERANCE:
            continue
        home = name_match_score(leg.home_team, event.home, abbreviation_only=True)
        away = name_match_score(leg.away_team, event.away, abbreviation_only=True)
        if home[0] < NAME_THRESHOLD or away[0] < NAME_THRESHOLD:
            continue
        key = (home[0] + away[0], home[1] + away[1], -abs((event.kickoff - kickoff).total_seconds()))
        ranked.append((key, event))
    if not ranked:
        return None
    ranked.sort(key=lambda row: row[0], reverse=True)
    if len(ranked) > 1 and ranked[1][0] == ranked[0][0]:
        return None
    return ranked[0][1]


# --- booking -----------------------------------------------------------------


class _BizRejected(BookingCodeError):
    """``orders/real-sports/order/share`` refused the whole request (a
    non-OK ``bizCode``). Distinct from the plain ``BookingCodeError`` a
    network failure, an HTTP error, or an unparseable reply raises: those
    say nothing about which selection is at fault, so retrying a smaller
    slip wouldn't help. Unlike SportyBet's reply, MSport's carries no
    per-selection ``unavailableOutcomes`` list even on success, so a
    ``bizCode`` refusal is the only signal this connector has that some
    selection in the slip is the problem -- which is why it's the one
    worth bisecting.
    """


class MSportConnector:
    def __init__(self, session: requests.Session | None = None) -> None:
        if session is None:
            self._session, self._events = _shared()
        else:
            self._session, self._events = session, _EventList(session)

    def create_code(self, legs: list[Leg]) -> ConnectorResult:
        translated = {leg.match_id: translate(leg.market, leg.selection) for leg in legs}
        known = [leg for leg in legs if translated[leg.match_id] is not None]
        event_ids = self._events.find_all(known) if known else {}

        selections: list[dict] = []
        selection_match_ids: list[int] = []
        booked: dict[str, int] = {}
        for leg in known:
            event_id = event_ids.get(leg.match_id)
            if event_id is None:
                continue
            market_id, specifier, outcome_id = translated[leg.match_id]
            selections.append({
                "eventId": event_id, "marketId": int(market_id), "specifier": specifier or "", "outcomeId": outcome_id,
            })
            selection_match_ids.append(leg.match_id)
            booked[event_id] = leg.match_id

        if not selections:
            raise BookingCodeError(
                f"{SITE_NAME} doesn't list any of these picks right now, so there's nothing to book there -- "
                "the matches may not be open on it yet, or the markets aren't ones this connection books."
            )

        try:
            data, rejected_indices = self._share_dropping_bad(list(enumerate(selections)))
        except _BizRejected as exc:
            raise BookingCodeError(str(exc)) from exc
        site_rejected = {selection_match_ids[i] for i in rejected_indices}

        code = data.get("shareCode")
        if not code:
            raise BookingCodeError(f"{SITE_NAME} accepted the slip but sent back no booking code.")

        included = set(booked.values()) - site_rejected
        reasons = {}
        for leg in legs:
            if translated[leg.match_id] is None:
                reasons[leg.match_id] = f"{SITE_NAME} has no bet that settles like {leg.market}: {leg.selection}."
            elif leg.match_id in site_rejected:
                reasons[leg.match_id] = f"{SITE_NAME} doesn't offer this exact market for this match, so it was left out."
            elif leg.match_id not in included:
                reasons[leg.match_id] = f"Not listed on {SITE_NAME} right now."
        return ConnectorResult(
            code=str(code), link=None,
            unavailable_match_ids=[leg.match_id for leg in legs if leg.match_id not in included],
            reasons=reasons,
        )

    def _share_dropping_bad(self, items: list[tuple[int, dict]]) -> tuple[dict, set[int]]:
        """Book as much of ``items`` as MSport will take in one slip.

        Mirrors sportybet.SportyBetConnector._share_dropping_bad exactly:
        ``items`` pairs each selection with its position in the caller's
        original list, so a dropped selection can be reported back by that
        position however deep the recursion goes. Returns the successful
        reply and the positions dropped because a ``bizCode`` refusal,
        isolated by bisection, pinned the fault on them -- never a position
        that merely sat in a request that also had other things wrong with it.
        """
        try:
            return self._share([selection for _, selection in items]), set()
        except _BizRejected:
            if len(items) == 1:
                raise

        mid = len(items) // 2
        dropped: set[int] = set()
        kept: list[tuple[int, dict]] = []
        for half in (items[:mid], items[mid:]):
            try:
                _, half_dropped = self._share_dropping_bad(half)
            except _BizRejected:
                dropped.update(index for index, _ in half)
                continue
            dropped |= half_dropped
            kept.extend(item for item in half if item[0] not in half_dropped)

        if not kept:
            raise _BizRejected(f"{SITE_NAME} has none of these picks open for booking right now.")
        return self._share([selection for _, selection in kept]), dropped

    def _share(self, selections: list[dict]) -> dict:
        try:
            response = self._session.post(
                SHARE_URL, json={"selections": selections},
                headers={**_API_HEADERS, "Content-Type": "application/json"}, timeout=TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise BookingCodeError(f"Couldn't reach {SITE_NAME} from this server ({type(exc).__name__}).") from exc
        if response.status_code != 200:
            raise BookingCodeError(f"{SITE_NAME} refused the booking (HTTP {response.status_code}).")
        try:
            payload = response.json()
        except ValueError as exc:
            raise BookingCodeError(f"{SITE_NAME} sent a booking reply this connection can't read.") from exc
        if payload.get("bizCode") != _OK:
            raise _BizRejected(f"{SITE_NAME} wouldn't book this slip: {payload.get('message') or 'no reason given'}.")
        return payload.get("data") or {}


# One session and one event list per process, so the five-minute reuse above
# holds across bookings rather than per connector instance.
_shared_state: tuple[requests.Session, _EventList] | None = None
_shared_lock = threading.Lock()


def _shared() -> tuple[requests.Session, _EventList]:
    global _shared_state
    with _shared_lock:
        if _shared_state is None:
            session = requests.Session()
            _shared_state = (session, _EventList(session))
        return _shared_state
