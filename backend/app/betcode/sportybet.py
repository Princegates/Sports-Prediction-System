"""SportyBet Ghana: find each leg on the site and book the slip there.

Three steps, each against an endpoint SportyBet's own web page uses and that
answers without an account:

1. **Find the match.** A leg in a league we know SportyBet's id for is
   looked up in that league's own list (``factsCenter/pcEvents``), which
   runs a couple of rounds ahead; anything else, or anything that list
   lacks, in the cross-league ``factsCenter/pcUpcomingEvents``, which shows
   only about the next round of each league. A leg is the event on the same
   date (a day either way -- kickoff times differ between sources) whose
   home *and* away teams both match ours by name. Both sides, in order,
   because one side alone is not evidence: "Aston Villa" scores a full match
   against "Villarreal" on its own. A leg with no such event, or with two
   equally good ones, is left out rather than guessed.
2. **Translate the pick.** Our market and selection become SportyBet's
   Sportradar market id, specifier and outcome id (``_MARKETS``). Only
   markets whose ids were read off the site itself are here; any other
   market is left out, never approximated by a neighbouring one.
3. **Book it.** ``orders/share`` takes the selections and returns the
   booking code, a link that opens the slip, and any selections it wouldn't
   include (a suspended market, a line it doesn't offer on that match).

Requests identify themselves as this application and carry only the headers
the API itself reads (client id, operator id, platform). Nothing here poses
as a browser or works around a refusal: if SportyBet turns this server away,
the member is told so, which is the answer a real connection gets.
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
import time
from dataclasses import dataclass

import requests

from app.betcode.providers import BookingCodeError
from app.betcode.selection import Leg
from app.betcode.sites import ConnectorResult
from app.data.team_matching import name_match_score

logger = logging.getLogger(__name__)

SITE_NAME = "SportyBet Ghana"
BASE_URL = "https://www.sportybet.com"
COUNTRY = "gh"
EVENTS_URL = f"{BASE_URL}/api/{COUNTRY}/factsCenter/pcUpcomingEvents"
LEAGUE_EVENTS_URL = f"{BASE_URL}/api/{COUNTRY}/factsCenter/pcEvents"
SHARE_URL = f"{BASE_URL}/api/{COUNTRY}/orders/share"
SHARE_LINK = f"{BASE_URL}/{COUNTRY}/?shareCode={{code}}"

FOOTBALL = "sr:sport:1"
# The market list the site's own football page asks for. The event lookup
# doesn't need prices, but asking for what the page asks for keeps this to a
# request the endpoint is known to answer.
EVENT_MARKETS = "1,18,10,29,11,26,36,14,60100"
# ...and what its league page asks for.
LEAGUE_MARKETS = "1,18,10,29,11,26,36,14"

# Our league -> SportyBet's (Sportradar's) tournament id. The first four were
# read off SportyBet itself; the rest are Sportradar's ids for those leagues,
# not yet seen on the site. A wrong one can't book the wrong match --
# find_event still needs both teams -- it only sends that league's legs on to
# the cross-league list.
TOURNAMENTS = {
    "English Premier League": "sr:tournament:17",
    "Spanish La Liga": "sr:tournament:8",
    "German Bundesliga": "sr:tournament:35",
    "UEFA Champions League": "sr:tournament:7",
    "English Championship": "sr:tournament:18",
    "Italian Serie A": "sr:tournament:23",
    "French Ligue 1": "sr:tournament:34",
    "Dutch Eredivisie": "sr:tournament:37",
    "Portuguese Primeira Liga": "sr:tournament:238",
    "Turkish Süper Lig": "sr:tournament:52",
}
PAGE_SIZE = 100
# ~1,200 upcoming football events at 100 a page. Paging stops as soon as
# every leg is found, so this is a ceiling, not the usual cost.
MAX_PAGES = 15
# The event list barely changes minute to minute, and a member often books
# the same slip twice (another site, a tweaked leg). Five minutes of reuse
# keeps this to a few requests an hour, not a few per booking.
EVENTS_CACHE_SECONDS = 300
TIMEOUT_SECONDS = 10.0

USER_AGENT = "SoccaIntel/1.0 (booking codes)"
_API_HEADERS = {
    "Accept": "*/*",
    "User-Agent": USER_AGENT,
    "clientid": "web",
    "operid": "3",
    "platform": "web",
}
_OK = 10000

# Same date, a day either way: sources disagree on kickoff times, and a
# fixture's two teams matching in order is what identifies it.
KICKOFF_TOLERANCE = dt.timedelta(days=1)
# Per side, on the less specific name -- the threshold the fixture importer
# uses for the same cross-source problem (app.data.api_football_ingest).
NAME_THRESHOLD = 0.82


# (our market, our selection) -> (marketId, specifier, outcomeId). Every id
# here was read from SportyBet's own responses; see the module docstring.
_MARKETS: dict[tuple[str, str], tuple[str, str | None, str]] = {
    ("Match Result", "Home Win"): ("1", None, "1"),
    ("Match Result", "Draw"): ("1", None, "2"),
    ("Match Result", "Away Win"): ("1", None, "3"),
    ("Double Chance", "Home/Draw"): ("10", None, "9"),
    ("Double Chance", "Home/Away"): ("10", None, "10"),
    ("Double Chance", "Draw/Away"): ("10", None, "11"),
    ("Both Teams To Score", "Yes"): ("29", None, "74"),
    ("Both Teams To Score", "No"): ("29", None, "76"),
    ("Draw No Bet", "Home"): ("11", None, "4"),
    ("Draw No Bet", "Away"): ("11", None, "5"),
    ("Total Goals Odd/Even", "Odd"): ("26", None, "70"),
    ("Total Goals Odd/Even", "Even"): ("26", None, "72"),
}
# "BTTS & Total Goals 2.5" / "Yes & Over 2.5" -> market 36, total=2.5.
_BTTS_TOTAL_OUTCOMES = {("Over", "Yes"): "90", ("Under", "Yes"): "92", ("Over", "No"): "94", ("Under", "No"): "96"}


def translate(market: str, selection: str) -> tuple[str, str | None, str] | None:
    """SportyBet's (marketId, specifier, outcomeId) for one of our picks, or
    None when this connection doesn't know the market."""

    fixed = _MARKETS.get((market, selection))
    if fixed is not None:
        return fixed

    if market.startswith("Total Goals ") and market != "Total Goals Odd/Even":
        line = market.removeprefix("Total Goals ")
        side = {f"Over {line}": "12", f"Under {line}": "13"}.get(selection)
        if side and _is_half_line(line):
            return "18", f"total={line}", side

    if market.startswith("BTTS & Total Goals "):
        line = market.removeprefix("BTTS & Total Goals ")
        btts, _, total = selection.partition(" & ")
        over_under = total.removesuffix(f" {line}")
        outcome = _BTTS_TOTAL_OUTCOMES.get((over_under, btts))
        if outcome and total.endswith(f" {line}") and _is_half_line(line):
            return "36", f"total={line}", outcome

    return None


def _is_half_line(line: str) -> bool:
    try:
        return float(line) % 1 == 0.5
    except ValueError:
        return False


# --- the event list ----------------------------------------------------------


@dataclass(frozen=True)
class Event:
    event_id: str
    home: str
    away: str
    kickoff: dt.datetime  # naive UTC, like Match.date


def _parse_events(payload: dict) -> tuple[list[Event], int]:
    if payload.get("bizCode") != _OK:
        raise BookingCodeError(f"{SITE_NAME} wouldn't list its matches: {payload.get('message') or 'no reason given'}.")
    data = payload.get("data") or {}
    # The league list sends its tournaments as a bare list; the cross-league
    # one wraps them with a total count.
    tournaments = data if isinstance(data, list) else data.get("tournaments") or []
    events = []
    for tournament in tournaments:
        for raw in tournament.get("events") or []:
            try:
                events.append(Event(
                    event_id=str(raw["eventId"]),
                    home=str(raw["homeTeamName"]),
                    away=str(raw["awayTeamName"]),
                    kickoff=dt.datetime.fromtimestamp(int(raw["estimateStartTime"]) / 1000, dt.timezone.utc).replace(tzinfo=None),
                ))
            except (KeyError, TypeError, ValueError):
                continue
    return events, 0 if isinstance(data, list) else int(data.get("totalNum") or 0)


class _EventList:
    """Upcoming events: each league's own list, fetched once, and the
    cross-league list a page at a time and only as far as needed."""

    def __init__(self, session: requests.Session) -> None:
        self._session = session
        self._lock = threading.Lock()
        self._reset()

    def _reset(self) -> None:
        self.events: list[Event] = []
        self._leagues: dict[str, list[Event]] = {}
        self._seen: set[str] = set()
        self._pages = 0
        self._exhausted = False
        self._fetched_at = time.monotonic()

    def _fetch_league(self, tournament_id: str) -> list[Event]:
        body = [{"sportId": FOOTBALL, "marketId": LEAGUE_MARKETS, "tournamentId": [[tournament_id]]}]
        try:
            response = self._session.post(
                LEAGUE_EVENTS_URL, json=body, headers={**_API_HEADERS, "Content-Type": "application/json"},
                timeout=TIMEOUT_SECONDS,
            )
            if response.status_code != 200:
                raise BookingCodeError(f"HTTP {response.status_code}")
            return _parse_events(response.json())[0]
        except (requests.RequestException, BookingCodeError, ValueError) as exc:
            # The cross-league list still covers the next round, so a league
            # list that fails narrows the search rather than failing the
            # booking.
            logger.warning("%s league list %s unavailable: %s", SITE_NAME, tournament_id, exc)
            return []

    def _fetch_page(self, page: int) -> tuple[list[Event], int]:
        params = {
            "sportId": FOOTBALL, "marketId": EVENT_MARKETS, "pageSize": PAGE_SIZE,
            "pageNum": page, "option": 1, "_t": int(time.time() * 1000),
        }
        try:
            response = self._session.get(EVENTS_URL, params=params, headers=_API_HEADERS, timeout=TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            raise BookingCodeError(f"Couldn't reach {SITE_NAME} from this server ({type(exc).__name__}).") from exc
        if response.status_code != 200:
            raise BookingCodeError(f"{SITE_NAME} refused the match list (HTTP {response.status_code}).")
        try:
            return _parse_events(response.json())
        except ValueError as exc:
            raise BookingCodeError(f"{SITE_NAME} sent a match list this connection can't read.") from exc

    def find_all(self, legs: list[Leg]) -> dict[int, str]:
        """match_id -> SportyBet eventId for every leg found: first in each
        league's own list, then the rest in the cross-league one."""

        with self._lock:
            if time.monotonic() - self._fetched_at > EVENTS_CACHE_SECONDS:
                self._reset()
            found: dict[int, str] = {}
            for tournament_id in dict.fromkeys(TOURNAMENTS[l.league] for l in legs if l.league in TOURNAMENTS):
                if tournament_id not in self._leagues:
                    self._leagues[tournament_id] = self._fetch_league(tournament_id)
                in_league = [l for l in legs if TOURNAMENTS.get(l.league) == tournament_id]
                found.update(_match_legs(in_league, self._leagues[tournament_id]))
            rest = [l for l in legs if l.match_id not in found]
            if rest:
                found.update(self._find_upcoming(rest))
            return found

    def _find_upcoming(self, legs: list[Leg]) -> dict[int, str]:
        found = _match_legs(legs, self.events)
        while len(found) < len(legs) and not self._exhausted:
            page_events, total = self._fetch_page(self._pages + 1)
            self._pages += 1
            new = [e for e in page_events if e.event_id not in self._seen]
            self._seen.update(e.event_id for e in new)
            self.events.extend(new)
            # A page that adds nothing means paging has stopped moving,
            # whatever totalNum says.
            if not new or self._pages * PAGE_SIZE >= total or self._pages >= MAX_PAGES:
                self._exhausted = True
            found = _match_legs(legs, self.events)
        return found


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
    better-covered names win and then the nearer kickoff; an exact tie between
    two different events is refused, because picking one would book a bet on
    a match the member didn't choose.
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


def _unavailable_event_ids(data: dict) -> set[str]:
    ids = set()
    for entry in data.get("unavailableOutcomes") or []:
        if isinstance(entry, dict) and entry.get("eventId"):
            ids.add(str(entry["eventId"]))
    return ids


class SportyBetConnector:
    def __init__(self, session: requests.Session | None = None) -> None:
        if session is None:
            self._session, self._events = _shared()
        else:
            self._session, self._events = session, _EventList(session)

    def create_code(self, legs: list[Leg]) -> ConnectorResult:
        translated = {leg.match_id: translate(leg.market, leg.selection) for leg in legs}
        known = [leg for leg in legs if translated[leg.match_id] is not None]
        event_ids = self._events.find_all(known) if known else {}

        selections, booked = [], {}
        for leg in known:
            event_id = event_ids.get(leg.match_id)
            if event_id is None:
                continue
            market_id, specifier, outcome_id = translated[leg.match_id]
            selections.append({"eventId": event_id, "marketId": market_id, "specifier": specifier, "outcomeId": outcome_id})
            booked[event_id] = leg.match_id

        if not selections:
            raise BookingCodeError(
                f"{SITE_NAME} doesn't list any of these picks right now, so there's nothing to book there -- "
                "the matches may not be open on it yet, or the markets aren't ones this connection books."
            )

        data = self._share(selections)
        code = data.get("shareCode")
        if not code:
            raise BookingCodeError(f"{SITE_NAME} accepted the slip but sent back no booking code.")

        refused = {booked[e] for e in _unavailable_event_ids(data) if e in booked}
        if len(refused) == len(booked):
            raise BookingCodeError(f"{SITE_NAME} has none of these picks open for booking right now.")

        link = str(data.get("shareURL") or SHARE_LINK.format(code=code))
        if link.startswith("http://"):
            link = "https://" + link.removeprefix("http://")
        included = set(booked.values()) - refused
        return ConnectorResult(
            code=str(code), link=link,
            unavailable_match_ids=[leg.match_id for leg in legs if leg.match_id not in included],
        )

    def _share(self, selections: list[dict]) -> dict:
        try:
            response = self._session.post(
                SHARE_URL, json={"selections": selections},
                headers={**_API_HEADERS, "Content-Type": "application/json;charset=UTF-8"}, timeout=TIMEOUT_SECONDS,
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
            raise BookingCodeError(f"{SITE_NAME} wouldn't book this slip: {payload.get('message') or 'no reason given'}.")
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
