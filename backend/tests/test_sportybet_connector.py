"""The SportyBet Ghana connection, against canned SportyBet responses.

Nothing here contacts SportyBet: the HTTP session is a fake. The events and
ids below are real ones from SportyBet's own match list and booking reply
(fixture data only -- no account, cookie or session detail), so the tests pin
the shapes the live site actually sends.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest
import requests

from app.betcode import sites
from app.betcode import sportybet as sb
from app.betcode.providers import BookingCodeError
from app.betcode.selection import Leg


def _ms(when: dt.datetime) -> int:
    return int(when.replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


MAN_UTD_SPURS = dt.datetime(2026, 10, 10, 16, 30)
VILLA_BRENTFORD = dt.datetime(2026, 10, 10, 14, 0)
REAL_VILLARREAL = dt.datetime(2026, 10, 10, 19, 0)
KOLN_GLADBACH = dt.datetime(2026, 10, 11, 13, 30)


def _event(event_id: str, home: str, away: str, kickoff: dt.datetime) -> dict:
    return {
        "eventId": event_id, "gameId": "1", "estimateStartTime": _ms(kickoff), "status": 0,
        "matchStatus": "Not start", "homeTeamName": home, "awayTeamName": away, "markets": [],
        "bookingStatus": "Booked",
    }


def _events_page(*tournaments: tuple[str, list[dict]], total: int = 4) -> dict:
    return {
        "bizCode": 10000, "message": "0#0",
        "data": {"totalNum": total, "tournaments": [
            {"id": tid, "name": tid, "events": events} for tid, events in tournaments
        ]},
    }


PAGE_ONE = _events_page(
    ("sr:tournament:17", [
        _event("sr:match:72221308", "Man Utd", "Tottenham", MAN_UTD_SPURS),
        _event("sr:match:72221294", "Aston Villa", "Brentford", VILLA_BRENTFORD),
    ]),
    ("sr:tournament:8", [_event("sr:match:72478622", "Real Madrid", "Villarreal", REAL_VILLARREAL)]),
    ("sr:tournament:35", [_event("sr:match:72513234", "Cologne", "Borussia M´gladbach", KOLN_GLADBACH)]),
)


def _share_reply(code: str = "9Y8YN0", unavailable: list[dict] | None = None) -> dict:
    return {
        "bizCode": 10000, "isAvailable": True, "message": "Success",
        "data": {
            "shareCode": code, "shareURL": f"http://www.sportybet.com/gh/?shareCode={code}",
            "ticket": {"selections": []}, "deadline": 1791678600000, "outcomes": [],
            "unavailableOutcomes": unavailable or [],
        },
    }


class _Response:
    def __init__(self, status_code: int = 200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _Session:
    def __init__(self, pages=None, share=None, get_error=None, leagues=None):
        self.pages = pages if pages is not None else [_Response(payload=PAGE_ONE)]
        self.share = share if share is not None else _Response(payload=_share_reply())
        self.get_error = get_error
        # tournament id -> league-list response; a league not given answers
        # with an empty list.
        self.leagues = leagues or {}
        self.gets: list[dict] = []
        self.posts: list[dict] = []
        self.league_posts: list[dict] = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.gets.append({"url": url, "params": params, "headers": headers})
        if self.get_error:
            raise self.get_error
        page = params["pageNum"]
        return self.pages[page - 1] if page <= len(self.pages) else _Response(payload=_events_page(total=0))

    def post(self, url, json=None, headers=None, timeout=None):
        if url == sb.LEAGUE_EVENTS_URL:
            self.league_posts.append({"json": json, "headers": headers})
            return self.leagues.get(json[0]["tournamentId"][0][0], _Response(payload={"bizCode": 10000, "data": []}))
        self.posts.append({"url": url, "json": json, "headers": headers})
        return self.share


def _leg(match_id: int, home: str, away: str, kickoff: dt.datetime, market="Match Result", selection="Home Win") -> Leg:
    return Leg(match_id=match_id, league="Any", home_team=home, away_team=away, kickoff=kickoff,
               market=market, selection=selection, model_probability=0.6)


# --- translating our picks ---------------------------------------------------


@pytest.mark.parametrize("market,selection,expected", [
    ("Match Result", "Home Win", ("1", None, "1")),
    ("Match Result", "Draw", ("1", None, "2")),
    ("Match Result", "Away Win", ("1", None, "3")),
    ("Double Chance", "Draw/Away", ("10", None, "11")),
    ("Both Teams To Score", "No", ("29", None, "76")),
    ("Draw No Bet", "Away", ("11", None, "5")),
    ("Total Goals 2.5", "Over 2.5", ("18", "total=2.5", "12")),
    ("Total Goals 1.5", "Under 1.5", ("18", "total=1.5", "13")),
    ("Total Goals Odd/Even", "Even", ("26", None, "72")),
    ("BTTS & Total Goals 2.5", "Yes & Over 2.5", ("36", "total=2.5", "90")),
    ("BTTS & Total Goals 2.5", "No & Under 2.5", ("36", "total=2.5", "96")),
])
def test_known_picks_translate_to_sportybet_ids(market, selection, expected):
    assert sb.translate(market, selection) == expected


@pytest.mark.parametrize("market,selection,expected", [
    ("Correct Score", "2-1", ("45", None, "288")),
    ("HT Result", "Home", ("60", None, "1")),
    ("Home Goals 1.5", "Over 1.5", ("19", "total=1.5", "12")),
    ("HT/FT", "X/1", ("47", None, "424")),
    ("HT/FT & Exact Goals", "2/X & 2 goals", ("820", None, "1864")),
    # Same bet under SportyBet's own name for it.
    ("Both Teams Clean Sheet", "Yes", ("18", "total=0.5", "13")),
    ("Total Goals Range", "5+ goals", ("18", "total=4.5", "12")),
    ("Result & Clean Sheet", "Home & Clean Sheet Yes", ("33", None, "74")),
])
def test_the_wider_markets_translate_too(market, selection, expected):
    assert sb.translate(market, selection) == expected


@pytest.mark.parametrize("market,selection", [
    # Ours counts full-time goals; SportyBet's lookalike counts first-half ones.
    ("HT Result & Total Goals 1.5", "Home & Over 1.5"),
    ("HT Result & BTTS", "Home & BTTS Yes"),
    ("HT Double Chance & BTTS", "Home/Draw & BTTS Yes"),
    # SportyBet's margin buckets are 1, 2, 3+; ours 1, 2, 3, 4+.
    ("Winning Margin", "Home by exactly 3"),
    ("Winning Margin", "Away by 4+"),
    ("Correct Score", "5-1"),  # SportyBet's "Other" isn't the score picked
    ("Result & Clean Sheet", "Home & Clean Sheet No"),
    ("HT/FT & BTTS", "1/1 & BTTS Yes"),
    ("Total Goals 2.5", "Over 3.5"),  # a selection from a different line
    ("Total Goals 2", "Over 2"),  # a whole line settles differently (a push)
])
def test_a_pick_without_an_identical_sportybet_bet_is_not_approximated(market, selection):
    assert sb.translate(market, selection) is None


# --- the table against SportyBet's own market list -----------------------------

_CAPTURE = json.loads((Path(__file__).parent / "fixtures" / "sportybet_event_markets.json").read_text())
_ON_SITE = {(m["id"], m["specifier"]): m["outcomes"] for m in _CAPTURE["markets"]}
# Outcome ids mean the same on every line of a market; a line leaves out the
# combinations it makes impossible (a half-time home lead can't become an away
# win with under 2.5 goals), so existence is checked across all its lines.
_OUTCOMES_BY_MARKET: dict[str, set[str]] = {}
for _m in _CAPTURE["markets"]:
    _OUTCOMES_BY_MARKET.setdefault(_m["id"], set()).update(_m["outcomes"])


def test_every_translation_names_an_outcome_sportybet_offers():
    """Each (market, specifier, outcome) must exist on SportyBet's real match
    page. Lines a match doesn't happen to carry (total=6.5) are skipped;
    markets without a line must all be there, which catches a mistyped id."""

    checked = 0
    for (market, selection), (market_id, specifier, outcome_id) in sb._MARKETS.items():
        offered = _ON_SITE.get((market_id, specifier))
        if offered is None:
            assert specifier and specifier.startswith("total="), f"{market} / {selection}: no SportyBet market {market_id} {specifier}"
            continue
        assert outcome_id in _OUTCOMES_BY_MARKET[market_id], f"{market} / {selection}: SportyBet {market_id} has no outcome {outcome_id}"
        checked += 1
    assert checked > 200


_HT_FT = {"1": "Home", "X": "Draw", "2": "Away"}


def _expected_desc(market: str, selection: str) -> str | None:
    """What SportyBet calls our selection, for markets named the same way on
    both sides. None for the ones booked under SportyBet's own name for the
    same bet (checked by hand in the table's comments)."""

    if market in ("Correct Score", "HT Correct Score"):
        return selection.replace("-", ":")
    if market in ("Double Chance", "HT Double Chance"):
        return selection.replace("/", " or ")
    if market == "HT/FT":
        ht, ft = selection.split("/")
        return f"{_HT_FT[ht]}/{_HT_FT[ft]}"
    if market.startswith("HT/FT & Total Goals"):
        htft, total = selection.split(" & ")
        ht, ft = htft.split("/")
        return f"{_HT_FT[ht]}/{_HT_FT[ft]} & {total}".lower()
    if market == "HT/FT & Exact Goals":
        htft, goals = selection.split(" & ")
        ht, ft = htft.split("/")
        return f"{_HT_FT[ht]}/{_HT_FT[ft]} & {goals.split(' ')[0]}".lower()
    if market == "Result & BTTS":
        return selection.replace("BTTS ", "")
    if market == "Winning Margin":
        return selection.replace("exactly ", "")
    if market == "Total Goals Range":
        return None if selection == "5+ goals" else selection.split(" ")[0]
    if market.startswith(("Total Goals ", "Home Goals ", "Away Goals ", "HT Total Goals ",
                          "Result & Total Goals ", "BTTS & Total Goals ", "HT Exact Goals", "HT Result",
                          "Match Result", "Both Teams To Score", "HT Both Teams To Score", "Draw No Bet",
                          "Half With Most Goals")) and "Odd/Even" not in market:
        if market.startswith("BTTS & Total Goals "):
            btts, total = selection.split(" & ")
            return f"{total} & {btts}"
        return selection.removesuffix(" Win").replace("1st Half", "1st half").replace("2nd Half", "2nd half")
    if "Odd/Even" in market or market in ("Home Clean Sheet", "Away Clean Sheet"):
        return selection
    return None


def test_each_translation_means_what_we_picked():
    """The outcome's name on SportyBet has to say what our selection says --
    "2-1" is "2:1", "X/1" is "Draw/Home" -- not merely exist."""

    team = {"Arsenal": "Home", "Leeds United": "Away", "draw": "Draw"}
    compared = 0
    for (market, selection), (market_id, specifier, outcome_id) in sb._MARKETS.items():
        expected = _expected_desc(market, selection)
        offered = _ON_SITE.get((market_id, specifier))
        if expected is None or offered is None or outcome_id not in offered:
            continue
        actual = offered[outcome_id]
        if market == "HT/FT & Exact Goals":  # SportyBet names the teams here
            htft, goals = actual.split(" & ")
            actual = "/".join(team[side] for side in htft.split("/")) + f" & {goals}"
        assert actual.lower() == expected.lower(), f"{market} / {selection}: SportyBet calls it {actual!r}"
        compared += 1
    assert compared > 150


# --- finding the match -------------------------------------------------------


def _events():
    return sb._parse_events(PAGE_ONE)[0]


def test_a_leg_is_found_despite_the_site_spelling_its_teams_differently():
    united = _leg(1, "Manchester United FC", "Tottenham Hotspur FC", MAN_UTD_SPURS)
    koln = _leg(2, "1. FC Köln", "Borussia Mönchengladbach", KOLN_GLADBACH)

    assert sb.find_event(united, _events()).event_id == "sr:match:72221308"
    assert sb.find_event(koln, _events()).event_id == "sr:match:72513234"


def test_kickoff_times_may_differ_by_hours_but_not_days():
    shifted = _leg(1, "Manchester United FC", "Tottenham Hotspur FC", MAN_UTD_SPURS + dt.timedelta(hours=5))
    next_week = _leg(1, "Manchester United FC", "Tottenham Hotspur FC", MAN_UTD_SPURS + dt.timedelta(days=7))

    assert sb.find_event(shifted, _events()) is not None
    assert sb.find_event(next_week, _events()) is None


def test_a_timezone_aware_kickoff_is_compared_in_utc():
    aware = MAN_UTD_SPURS.replace(tzinfo=dt.timezone.utc).astimezone(dt.timezone(dt.timedelta(hours=-5)))

    assert sb.find_event(_leg(1, "Manchester United FC", "Tottenham Hotspur FC", aware), _events()) is not None


def test_one_matching_team_is_not_enough():
    """"Aston Villa" scores a full match against "Villarreal" by itself, and
    both play on the same day -- only the opponent tells them apart."""

    villa_at_madrid = _leg(1, "Real Madrid", "Aston Villa", REAL_VILLARREAL)

    assert sb.find_event(villa_at_madrid, _events()) is None


def test_home_and_away_must_be_the_right_way_round():
    reversed_leg = _leg(1, "Tottenham Hotspur FC", "Manchester United FC", MAN_UTD_SPURS)

    assert sb.find_event(reversed_leg, _events()) is None


def test_two_equally_good_events_are_refused_not_guessed():
    events = [
        sb.Event("sr:match:1", "Man Utd", "Tottenham", MAN_UTD_SPURS),
        sb.Event("sr:match:2", "Man Utd", "Tottenham", MAN_UTD_SPURS),
    ]

    assert sb.find_event(_leg(1, "Manchester United FC", "Tottenham Hotspur FC", MAN_UTD_SPURS), events) is None


# --- booking -----------------------------------------------------------------


def test_a_slip_is_booked_and_its_code_returned():
    session = _Session()
    legs = [
        _leg(1, "Manchester United FC", "Tottenham Hotspur FC", MAN_UTD_SPURS),
        _leg(2, "Real Madrid", "Villarreal CF", REAL_VILLARREAL, "Total Goals 2.5", "Over 2.5"),
    ]

    result = sb.SportyBetConnector(session).create_code(legs)

    assert result.code == "9Y8YN0"
    assert result.link == "https://www.sportybet.com/gh/?shareCode=9Y8YN0"
    assert result.unavailable_match_ids == []
    assert session.posts[0]["url"] == sb.SHARE_URL
    assert session.posts[0]["json"] == {"selections": [
        {"eventId": "sr:match:72221308", "marketId": "1", "specifier": None, "outcomeId": "1"},
        {"eventId": "sr:match:72478622", "marketId": "18", "specifier": "total=2.5", "outcomeId": "12"},
    ]}


def test_legs_the_site_cant_take_are_named_and_the_rest_booked():
    session = _Session(share=_Response(payload=_share_reply(unavailable=[{"eventId": "sr:match:72478622"}])))
    legs = [
        _leg(1, "Manchester United FC", "Tottenham Hotspur FC", MAN_UTD_SPURS),
        _leg(2, "Real Madrid", "Villarreal CF", REAL_VILLARREAL),  # refused by the site
        _leg(3, "Aston Villa", "Brentford FC", VILLA_BRENTFORD, "HT Result & BTTS", "Home & BTTS Yes"),  # no such bet
        _leg(4, "Liverpool FC", "Chelsea FC", MAN_UTD_SPURS),  # not listed
    ]

    result = sb.SportyBetConnector(session).create_code(legs)

    assert result.code == "9Y8YN0"
    assert result.unavailable_match_ids == [2, 3, 4]
    assert "wouldn't take this pick" in result.reasons[2]
    assert result.reasons[3] == "SportyBet Ghana has no bet that settles like HT Result & BTTS: Home & BTTS Yes."
    assert result.reasons[4] == "Not listed on SportyBet Ghana right now."
    assert 1 not in result.reasons
    assert [s["eventId"] for s in session.posts[0]["json"]["selections"]] == ["sr:match:72221308", "sr:match:72478622"]


def test_nothing_bookable_says_so_without_asking_for_a_code():
    session = _Session()

    with pytest.raises(BookingCodeError, match="doesn't list any of these picks"):
        sb.SportyBetConnector(session).create_code([_leg(1, "Liverpool FC", "Chelsea FC", MAN_UTD_SPURS)])
    assert session.posts == []


def test_a_code_covering_none_of_the_picks_is_not_passed_on():
    session = _Session(share=_Response(payload=_share_reply(unavailable=[{"eventId": "sr:match:72221308"}])))

    with pytest.raises(BookingCodeError, match="none of these picks open"):
        sb.SportyBetConnector(session).create_code([_leg(1, "Manchester United FC", "Tottenham Hotspur FC", MAN_UTD_SPURS)])


def test_the_sites_refusal_reason_is_passed_on():
    session = _Session(share=_Response(payload={"bizCode": 4200, "message": "Selections have expired"}))

    with pytest.raises(BookingCodeError, match="Selections have expired"):
        sb.SportyBetConnector(session).create_code([_leg(1, "Manchester United FC", "Tottenham Hotspur FC", MAN_UTD_SPURS)])


@pytest.mark.parametrize("session,expected", [
    (_Session(get_error=requests.ConnectionError("refused")), "Couldn't reach SportyBet Ghana"),
    (_Session(pages=[_Response(403, {})]), "HTTP 403"),
    (_Session(pages=[_Response(payload=ValueError("not json"))]), "can't read"),
    (_Session(share=_Response(451, {})), "HTTP 451"),
])
def test_a_failure_is_reported_as_what_happened(session, expected):
    with pytest.raises(BookingCodeError, match=expected):
        sb.SportyBetConnector(session).create_code([_leg(1, "Manchester United FC", "Tottenham Hotspur FC", MAN_UTD_SPURS)])


def test_requests_identify_themselves_and_carry_no_browser_identity():
    session = _Session()

    sb.SportyBetConnector(session).create_code([_leg(1, "Manchester United FC", "Tottenham Hotspur FC", MAN_UTD_SPURS)])

    for sent in (session.gets[0]["headers"], session.posts[0]["headers"]):
        assert sent["User-Agent"] == sb.USER_AGENT
        assert {"operid", "clientid", "platform"} <= set(sent)
        assert not {"Cookie", "Origin", "Referer", "Authorization"} & set(sent)


# --- paging and reuse --------------------------------------------------------


def test_paging_stops_once_every_leg_is_found():
    page_two = _Response(payload=_events_page(
        ("sr:tournament:17", [_event("sr:match:9", "Liverpool", "Chelsea", MAN_UTD_SPURS)]), total=250,
    ))
    session = _Session(pages=[_Response(payload={**PAGE_ONE, "data": {**PAGE_ONE["data"], "totalNum": 250}}), page_two])
    connector = sb.SportyBetConnector(session)

    connector.create_code([_leg(1, "Manchester United FC", "Tottenham Hotspur FC", MAN_UTD_SPURS)])
    assert [g["params"]["pageNum"] for g in session.gets] == [1]

    connector.create_code([_leg(2, "Liverpool FC", "Chelsea FC", MAN_UTD_SPURS)])
    assert [g["params"]["pageNum"] for g in session.gets] == [1, 2]


def test_a_page_that_adds_nothing_ends_paging():
    """If the site keeps sending the same page, paging must not run on to
    the ceiling just because totalNum says there's more."""

    same = {**PAGE_ONE, "data": {**PAGE_ONE["data"], "totalNum": 5000}}
    session = _Session(pages=[_Response(payload=same)] * sb.MAX_PAGES)

    with pytest.raises(BookingCodeError):
        sb.SportyBetConnector(session).create_code([_leg(1, "Liverpool FC", "Chelsea FC", MAN_UTD_SPURS)])
    assert len(session.gets) == 2


def test_the_match_list_is_reused_for_a_few_minutes(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(sb.time, "monotonic", lambda: clock[0])
    session = _Session()
    connector = sb.SportyBetConnector(session)
    leg = _leg(1, "Manchester United FC", "Tottenham Hotspur FC", MAN_UTD_SPURS)

    connector.create_code([leg])
    connector.create_code([leg])
    assert len(session.gets) == 1

    clock[0] += sb.EVENTS_CACHE_SECONDS + 1
    connector.create_code([leg])
    assert len(session.gets) == 2


# --- league lists ------------------------------------------------------------

SPURS_COVENTRY = dt.datetime(2026, 10, 19, 19, 0)
LEEDS_UNITED = dt.datetime(2026, 10, 18, 13, 0)

# SportyBet's Premier League page: two rounds, in the bare-list shape that
# endpoint sends -- including matches the cross-league list above doesn't.
PREMIER_LEAGUE = {"bizCode": 10000, "message": "0#0", "data": [{
    "id": "sr:tournament:17", "name": "Premier League", "categoryName": "England", "categoryId": "sr:category:1",
    "events": [
        _event("sr:match:72221308", "Man Utd", "Tottenham", MAN_UTD_SPURS),
        _event("sr:match:72221322", "Leeds United", "Man Utd", LEEDS_UNITED),
        _event("sr:match:72221330", "Tottenham", "Coventry City", SPURS_COVENTRY),
    ],
}]}


def _epl_leg(match_id, home, away, kickoff, **kwargs) -> Leg:
    return Leg(match_id=match_id, league="English Premier League", home_team=home, away_team=away,
               kickoff=kickoff, market=kwargs.get("market", "Match Result"),
               selection=kwargs.get("selection", "Home Win"), model_probability=0.6)


def test_a_leg_is_found_in_its_own_leagues_list_beyond_the_next_round():
    session = _Session(leagues={"sr:tournament:17": _Response(payload=PREMIER_LEAGUE)})

    result = sb.SportyBetConnector(session).create_code([
        _epl_leg(1, "Tottenham Hotspur FC", "Coventry City FC", SPURS_COVENTRY),
        _epl_leg(2, "Leeds United FC", "Manchester United FC", LEEDS_UNITED),
    ])

    assert result.unavailable_match_ids == []
    assert [s["eventId"] for s in session.posts[0]["json"]["selections"]] == ["sr:match:72221330", "sr:match:72221322"]
    assert session.league_posts[0]["json"] == [
        {"sportId": "sr:sport:1", "marketId": "1,18,10,29,11,26,36,14", "tournamentId": [["sr:tournament:17"]]}
    ]
    assert session.gets == [], "every leg was in its league list, so the cross-league list isn't needed"
    sent = session.league_posts[0]["headers"]
    assert sent["User-Agent"] == sb.USER_AGENT and not {"Cookie", "Origin", "Referer"} & set(sent)


def test_a_leg_its_league_list_lacks_is_looked_for_in_the_cross_league_list():
    empty_league = _Response(payload={"bizCode": 10000, "data": [{"id": "sr:tournament:17", "events": []}]})
    session = _Session(leagues={"sr:tournament:17": empty_league})

    result = sb.SportyBetConnector(session).create_code(
        [_epl_leg(1, "Manchester United FC", "Tottenham Hotspur FC", MAN_UTD_SPURS)]
    )

    assert result.unavailable_match_ids == []
    assert len(session.league_posts) == 1 and len(session.gets) == 1


def test_a_refused_league_list_narrows_the_search_rather_than_failing_the_booking():
    session = _Session(leagues={"sr:tournament:17": _Response(403, {})})

    result = sb.SportyBetConnector(session).create_code(
        [_epl_leg(1, "Manchester United FC", "Tottenham Hotspur FC", MAN_UTD_SPURS)]
    )

    assert result.code == "9Y8YN0"


def test_a_league_without_a_known_id_goes_straight_to_the_cross_league_list():
    session = _Session()

    sb.SportyBetConnector(session).create_code(
        [_leg(1, "Manchester United FC", "Tottenham Hotspur FC", MAN_UTD_SPURS)]
    )

    assert session.league_posts == [] and len(session.gets) == 1


def test_each_league_list_is_fetched_once_for_a_few_minutes():
    session = _Session(leagues={"sr:tournament:17": _Response(payload=PREMIER_LEAGUE)})
    connector = sb.SportyBetConnector(session)

    connector.create_code([_epl_leg(1, "Tottenham Hotspur FC", "Coventry City FC", SPURS_COVENTRY)])
    connector.create_code([_epl_leg(2, "Leeds United FC", "Manchester United FC", LEEDS_UNITED)])

    assert len(session.league_posts) == 1


# --- registration ------------------------------------------------------------


def test_sportybet_ghana_is_registered_as_connected():
    assert sites.SITES_BY_KEY["sportybet_gh"].connected


def test_the_site_step_reports_what_the_code_leaves_out(monkeypatch):
    session = _Session()
    monkeypatch.setattr(sb, "_shared", lambda: (session, sb._EventList(session)))
    legs = [
        _leg(1, "Manchester United FC", "Tottenham Hotspur FC", MAN_UTD_SPURS),
        _leg(2, "Liverpool FC", "Chelsea FC", MAN_UTD_SPURS),
    ]

    code = sites.code_for_site("sportybet_gh", legs)

    assert code.status == "code_ready" and code.code == "9Y8YN0"
    assert code.unavailable_match_ids == [2]
    assert code.as_json()["unavailable_reasons"] == {"2": "Not listed on SportyBet Ghana right now."}
    assert "1 of these 2 picks" in code.message
