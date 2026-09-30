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
   Sportradar market id, specifier and outcome id (``_MARKETS``), read off
   the site's own match page. A pick translates only to a bet that settles
   the same way; one with no such bet is left out, never approximated by a
   neighbouring one.
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

from app.betcode.selection import Leg
from app.betcode.sites import BookingCodeError, ConnectorResult
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
    "UEFA Europa League": "sr:tournament:679",
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


# (our market, our selection) -> (marketId, specifier, outcomeId).
#
# Every id here was read off SportyBet's own match page (factsCenter/event,
# Arsenal vs Leeds United): Sportradar's market ids, whose outcome ids are
# the same on every match. A pick maps only to a SportyBet bet that settles
# identically -- sometimes under SportyBet's own name for it ("Both Teams
# Clean Sheet: Yes" is its "Under 0.5") -- and otherwise not at all. Four of
# ours look like SportyBet markets but aren't: our "HT Result & Total Goals",
# "HT Result & BTTS" and "HT Double Chance & BTTS" count full-time goals where
# SportyBet's count first-half ones, so they, like "HT/FT & BTTS", "HT + 2H
# Result" and "HT + FT Double Chance", have no entry.
Selection = tuple[str, str | None, str]
_RESULT = {"Home": 0, "Draw": 1, "Away": 2}
_SHORT = {"1": "Home", "X": "Draw", "2": "Away"}
_HALF_LINES = [f"{n}.5" for n in range(10)]


def _goal_label(n: str) -> str:
    return "5+ goals" if n == "5+" else f"{n} goal" + ("" if n == "1" else "s")


def _build_markets() -> dict[tuple[str, str], Selection]:
    t: dict[tuple[str, str], Selection] = {}

    def add(market: str, market_id: str, specifier: str | None, outcomes: dict[str, str]) -> None:
        for selection, outcome_id in outcomes.items():
            t[(market, selection)] = (market_id, specifier, outcome_id)

    yes_no = {"Yes": "74", "No": "76"}
    odd_even = {"Odd": "70", "Even": "72"}
    double_chance = {"Home/Draw": "9", "Home/Away": "10", "Draw/Away": "11"}

    # -- full time --------------------------------------------------------
    add("Match Result", "1", None, {"Home Win": "1", "Draw": "2", "Away Win": "3"})
    add("Double Chance", "10", None, double_chance)
    add("Both Teams To Score", "29", None, yes_no)
    add("Draw No Bet", "11", None, {"Home": "4", "Away": "5"})
    add("Total Goals Odd/Even", "26", None, odd_even)
    add("Home Goals Odd/Even", "27", None, odd_even)
    add("Away Goals Odd/Even", "28", None, odd_even)
    add("Home Clean Sheet", "31", None, yes_no)
    add("Away Clean Sheet", "32", None, yes_no)
    # Neither side conceding is no goals at all; otherwise at least one.
    add("Both Teams Clean Sheet", "18", "total=0.5", {"Yes": "13", "No": "12"})
    for line in _HALF_LINES:
        add(f"Total Goals {line}", "18", f"total={line}", {f"Over {line}": "12", f"Under {line}": "13"})
        add(f"Home Goals {line}", "19", f"total={line}", {f"Over {line}": "12", f"Under {line}": "13"})
        add(f"Away Goals {line}", "20", f"total={line}", {f"Over {line}": "12", f"Under {line}": "13"})
        add(f"BTTS & Total Goals {line}", "36", f"total={line}", {
            f"Yes & Over {line}": "90", f"Yes & Under {line}": "92",
            f"No & Over {line}": "94", f"No & Under {line}": "96",
        })
        add(f"Result & Total Goals {line}", "37", f"total={line}", {
            f"Home & Under {line}": "794", f"Home & Over {line}": "796",
            f"Draw & Under {line}": "798", f"Draw & Over {line}": "800",
            f"Away & Under {line}": "802", f"Away & Over {line}": "804",
        })
    add("Result & BTTS", "35", None, {
        "Home & BTTS Yes": "78", "Home & BTTS No": "80", "Draw & BTTS Yes": "82",
        "Draw & BTTS No": "84", "Away & BTTS Yes": "86", "Away & BTTS No": "88",
    })
    # Correct Score runs 0:0 to 4:4 (274, 276, ... 322: +2 per home goal,
    # +10 per away goal). Anything bigger is SportyBet's "Other", which isn't
    # the score picked.
    add("Correct Score", "45", None,
        {f"{h}-{a}": str(274 + 2 * (5 * a + h)) for h in range(5) for a in range(5)})
    # SportyBet's margin runs 1, 2, 3+; ours 1, 2, 3, 4+. Only the matching
    # buckets translate.
    add("Winning Margin", "15", "variant=sr:winning_margin:3+", {
        "Home by exactly 1": "sr:winning_margin:3+:113", "Home by exactly 2": "sr:winning_margin:3+:114",
        "Away by exactly 1": "sr:winning_margin:3+:116", "Away by exactly 2": "sr:winning_margin:3+:117",
        "Draw": "sr:winning_margin:3+:119",
    })
    add("Total Goals Range", "21", "variant=sr:exact_goals:6+",
        {_goal_label(str(n)): f"sr:exact_goals:6+:{68 + n}" for n in range(5)})
    add("Total Goals Range", "18", "total=4.5", {"5+ goals": "12"})
    # The winning side keeping a clean sheet is SportyBet's "win to nil"; a
    # clean-sheet draw is 0-0. The "No" halves have no single SportyBet bet.
    add("Result & Clean Sheet", "33", None, {"Home & Clean Sheet Yes": "74"})
    add("Result & Clean Sheet", "34", None, {"Away & Clean Sheet Yes": "74"})
    add("Result & Clean Sheet", "18", "total=0.5", {"Draw & Clean Sheet Yes": "13"})

    # -- half time --------------------------------------------------------
    add("HT Result", "60", None, {"Home": "1", "Draw": "2", "Away": "3"})
    add("HT Double Chance", "63", None, double_chance)
    add("HT Both Teams To Score", "75", None, yes_no)
    add("HT Total Goals Odd/Even", "74", None, odd_even)
    for line in _HALF_LINES:
        add(f"HT Total Goals {line}", "68", f"total={line}", {f"Over {line}": "12", f"Under {line}": "13"})
    add("HT Correct Score", "81", None, {
        "0-0": "462", "1-1": "464", "2-2": "466", "1-0": "468", "2-0": "470",
        "2-1": "472", "0-1": "474", "0-2": "476", "1-2": "478",
    })
    add("HT Exact Goals", "71", "variant=sr:exact_goals:3+",
        {k: f"sr:exact_goals:3+:{88 + i}" for i, k in enumerate(["0", "1", "2", "3+"])})
    add("HT Multigoals", "552", None, {"2-3": "1748", "4+": "1749"})
    add("HT Multigoals", "68", "total=1.5", {"0-1": "13"})

    # -- half time + full time --------------------------------------------
    for ht_short, ht in _SHORT.items():
        for ft_short, ft in _SHORT.items():
            i = 3 * _RESULT[ht] + _RESULT[ft]
            add("HT/FT", "47", None, {f"{ht_short}/{ft_short}": str(418 + 2 * i)})
            add("HT/FT & Total Goals 2.5", "818", "total=2.5", {
                f"{ht_short}/{ft_short} & Under 2.5": str(1836 + i),
                f"{ht_short}/{ft_short} & Over 2.5": str(1845 + i),
            })
    ht_ft_exact = {
        ("X/X", "0"): 1854,
        ("1/1", "1"): 1855, ("X/1", "1"): 1856, ("X/2", "1"): 1857, ("2/2", "1"): 1858,
        ("1/1", "2"): 1859, ("1/X", "2"): 1860, ("X/1", "2"): 1861, ("X/X", "2"): 1862,
        ("X/2", "2"): 1863, ("2/X", "2"): 1864, ("2/2", "2"): 1865,
        ("1/1", "3"): 1866, ("1/2", "3"): 1867, ("X/1", "3"): 1868, ("X/2", "3"): 1869,
        ("2/1", "3"): 1870, ("2/2", "3"): 1871,
    }
    for goals, first in (("4", 1872), ("5+", 1881)):
        for i, htft in enumerate(["1/1", "1/X", "1/2", "X/1", "X/X", "X/2", "2/1", "2/X", "2/2"]):
            ht_ft_exact[(htft, goals)] = first + i
    add("HT/FT & Exact Goals", "820", None,
        {f"{htft} & {_goal_label(goals)}": str(o) for (htft, goals), o in ht_ft_exact.items()})
    add("HT & 2H Total Goals 1.5", "58", "total=1.5", {"Over 1.5 HT & Over 1.5 2H": "74"})
    add("HT & 2H Total Goals 1.5", "59", "total=1.5", {"Under 1.5 HT & Under 1.5 2H": "74"})
    add("Half With Most Goals", "52", None, {"1st Half": "436", "2nd Half": "438", "Equal": "440"})
    return t


_MARKETS = _build_markets()


def translate(market: str, selection: str) -> Selection | None:
    """SportyBet's (marketId, specifier, outcomeId) for one of our picks, or
    None when SportyBet has no bet that settles the same way."""

    return _MARKETS.get((market, selection))


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


class _BizRejected(BookingCodeError):
    """``orders/share`` refused the whole request (a non-OK ``bizCode``).

    Distinct from the plain ``BookingCodeError`` a network failure, an HTTP
    error, or an unparseable reply raises: those say nothing about which
    selection is at fault, so retrying a smaller slip wouldn't help. A
    ``bizCode`` refusal often does come down to one selection SportyBet
    won't take (a line it doesn't list for that match) while the rest of
    the slip is fine, so this is the one worth bisecting.
    """


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

        selections: list[dict] = []
        selection_match_ids: list[int] = []
        booked: dict[str, int] = {}
        for leg in known:
            event_id = event_ids.get(leg.match_id)
            if event_id is None:
                continue
            market_id, specifier, outcome_id = translated[leg.match_id]
            selections.append({"eventId": event_id, "marketId": market_id, "specifier": specifier, "outcomeId": outcome_id})
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

        refused = {booked[e] for e in _unavailable_event_ids(data) if e in booked}
        if len(refused) + len(site_rejected) == len(booked):
            raise BookingCodeError(f"{SITE_NAME} has none of these picks open for booking right now.")

        link = str(data.get("shareURL") or SHARE_LINK.format(code=code))
        if link.startswith("http://"):
            link = "https://" + link.removeprefix("http://")
        included = set(booked.values()) - refused - site_rejected
        reasons = {}
        for leg in legs:
            if translated[leg.match_id] is None:
                reasons[leg.match_id] = f"{SITE_NAME} has no bet that settles like {leg.market}: {leg.selection}."
            elif leg.match_id in site_rejected:
                reasons[leg.match_id] = f"{SITE_NAME} doesn't offer this exact market for this match, so it was left out."
            elif leg.match_id in refused:
                reasons[leg.match_id] = (
                    f"{SITE_NAME} wouldn't take this pick -- the market may be closed or not offered for this match."
                )
            elif leg.match_id not in included:
                reasons[leg.match_id] = f"Not listed on {SITE_NAME} right now."
        return ConnectorResult(
            code=str(code), link=link,
            unavailable_match_ids=[leg.match_id for leg in legs if leg.match_id not in included],
            reasons=reasons,
        )

    def _share_dropping_bad(self, items: list[tuple[int, dict]]) -> tuple[dict, set[int]]:
        """Book as much of ``items`` as SportyBet will take in one slip.

        ``items`` pairs each selection with its position in the caller's
        original list, so a dropped selection can be reported back by that
        position however deep the recursion goes. Returns the successful
        reply and the positions dropped because a ``bizCode`` refusal, isolated
        by bisection, pinned the fault on them -- never a position that
        merely sat in a request that also had other things wrong with it.
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
