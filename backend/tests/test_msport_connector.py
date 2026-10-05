"""The MSport Ghana connection, against canned MSport responses.

Nothing here contacts MSport: the HTTP session is a fake. The events and
ids below are real ones from MSport's own fixtures list and booking reply
(fixture data only -- no account, cookie or session detail), so the tests
pin the shapes the live site actually sends.
"""

from __future__ import annotations

import datetime as dt

import pytest
import requests

from app.betcode import msport as ms
from app.betcode import sites
from app.betcode.selection import Leg
from app.betcode.sites import BookingCodeError


def _ms_time(when: dt.datetime) -> int:
    return int(when.replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


MAN_UTD_SPURS = dt.datetime(2026, 10, 10, 16, 30)
VILLA_BRENTFORD = dt.datetime(2026, 10, 10, 14, 0)
REAL_VILLARREAL = dt.datetime(2026, 10, 10, 19, 0)
KOLN_GLADBACH = dt.datetime(2026, 10, 11, 13, 30)


def _event(event_id: str, home: str, away: str, kickoff: dt.datetime) -> dict:
    return {
        "eventId": event_id, "homeTeam": home, "awayTeam": away, "startTime": _ms_time(kickoff),
        "status": 0, "markets": [], "marketsCount": 0,
    }


def _fixtures_list(*tournaments: tuple[str, list[dict]]) -> dict:
    return {
        "bizCode": 10000, "innerMsg": "success", "message": "success",
        "data": {"events": [], "lastEventId": "sr:match:1", "productStatus": [], "tournaments": [
            {"tournament": tid, "tournamentId": tid, "events": events} for tid, events in tournaments
        ]},
    }


FIXTURES = _fixtures_list(
    ("Premier League", [
        _event("sr:match:72221308", "Man Utd", "Tottenham", MAN_UTD_SPURS),
        _event("sr:match:72221294", "Aston Villa", "Brentford", VILLA_BRENTFORD),
    ]),
    ("La Liga", [_event("sr:match:72478622", "Real Madrid", "Villarreal", REAL_VILLARREAL)]),
    ("Bundesliga", [_event("sr:match:72513234", "Cologne", "Borussia M´gladbach", KOLN_GLADBACH)]),
)


def _share_reply(code: str = "B3T2LV8", without_user: str = "B3T2LV7") -> dict:
    return {
        "bizCode": 10000, "innerMsg": "success", "message": "success",
        "data": {
            "bettableBetSlip": None, "followedTimes": 0, "message": None, "operId": None,
            "originalSelectionCount": 0, "rank": 0, "relatedBettableBetSlip": None,
            "shareCode": code, "shareCodeWithoutUser": without_user, "showFollowedTimes": 0,
            "showRank": 0, "ticket": None, "userId": None,
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
    def __init__(self, fixtures=None, share=None, get_error=None):
        self.fixtures = fixtures if fixtures is not None else _Response(payload=FIXTURES)
        self.share = share if share is not None else _Response(payload=_share_reply())
        self.get_error = get_error
        self.gets: list[dict] = []
        self.posts: list[dict] = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.gets.append({"url": url, "params": params, "headers": headers})
        if self.get_error:
            raise self.get_error
        return self.fixtures

    def post(self, url, json=None, headers=None, timeout=None):
        self.posts.append({"url": url, "json": json, "headers": headers})
        return self.share(json["selections"]) if callable(self.share) else self.share


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
    ("Total Goals Odd/Even", "Even", ("26", None, "72")),
    ("Correct Score", "2-1", ("45", None, "288")),
    ("HT/FT", "X/1", ("47", None, "424")),
    ("Winning Margin", "Home by exactly 1", ("15", "variant=sr:winning_margin:3+", "sr:winning_margin:3+:113")),
])
def test_known_picks_translate_to_msport_ids(market, selection, expected):
    assert ms.translate(market, selection) == expected


def test_total_goals_range_is_not_translated_pending_a_confirmed_mapping():
    """MSport reads this market under a different Sportradar variant
    (sr:exact_goals:5+ vs SportyBet's 6+) whose outcome ids were never read
    off MSport's own page -- left out rather than guessed."""

    assert ms.translate("Total Goals Range", "0 goals") is None
    assert ("Total Goals Range", "0 goals") not in ms._MARKETS


def test_a_pick_without_an_identical_bet_is_not_approximated():
    assert ms.translate("Winning Margin", "Home by exactly 3") is None
    assert ms.translate("HT Result & BTTS", "Home & BTTS Yes") is None


# --- finding the match -------------------------------------------------------


def _events():
    return ms._parse_events(FIXTURES)


def test_a_leg_is_found_despite_the_site_spelling_its_teams_differently():
    united = _leg(1, "Manchester United FC", "Tottenham Hotspur FC", MAN_UTD_SPURS)
    koln = _leg(2, "1. FC Köln", "Borussia Mönchengladbach", KOLN_GLADBACH)

    assert ms.find_event(united, _events()).event_id == "sr:match:72221308"
    assert ms.find_event(koln, _events()).event_id == "sr:match:72513234"


def test_kickoff_times_may_differ_by_hours_but_not_days():
    shifted = _leg(1, "Manchester United FC", "Tottenham Hotspur FC", MAN_UTD_SPURS + dt.timedelta(hours=5))
    next_week = _leg(1, "Manchester United FC", "Tottenham Hotspur FC", MAN_UTD_SPURS + dt.timedelta(days=7))

    assert ms.find_event(shifted, _events()) is not None
    assert ms.find_event(next_week, _events()) is None


def test_one_matching_team_is_not_enough():
    villa_at_madrid = _leg(1, "Real Madrid", "Aston Villa", REAL_VILLARREAL)

    assert ms.find_event(villa_at_madrid, _events()) is None


def test_two_equally_good_events_are_refused_not_guessed():
    events = [
        ms.Event("sr:match:1", "Man Utd", "Tottenham", MAN_UTD_SPURS),
        ms.Event("sr:match:2", "Man Utd", "Tottenham", MAN_UTD_SPURS),
    ]

    assert ms.find_event(_leg(1, "Manchester United FC", "Tottenham Hotspur FC", MAN_UTD_SPURS), events) is None


# --- booking -----------------------------------------------------------------


def test_a_slip_is_booked_and_its_code_returned():
    session = _Session()
    legs = [
        _leg(1, "Manchester United FC", "Tottenham Hotspur FC", MAN_UTD_SPURS),
        _leg(2, "Real Madrid", "Villarreal CF", REAL_VILLARREAL, "Total Goals 2.5", "Over 2.5"),
    ]

    result = ms.MSportConnector(session).create_code(legs)

    assert result.code == "B3T2LV8"
    assert result.link is None
    assert result.unavailable_match_ids == []
    assert session.posts[0]["url"] == ms.SHARE_URL
    assert session.posts[0]["json"] == {"selections": [
        {"eventId": "sr:match:72221308", "marketId": 1, "specifier": "", "outcomeId": "1"},
        {"eventId": "sr:match:72478622", "marketId": 18, "specifier": "total=2.5", "outcomeId": "12"},
    ]}


def test_legs_the_site_has_no_bet_for_are_named_and_the_rest_booked():
    session = _Session()
    legs = [
        _leg(1, "Manchester United FC", "Tottenham Hotspur FC", MAN_UTD_SPURS),
        _leg(2, "Aston Villa", "Brentford FC", VILLA_BRENTFORD, "HT Result & BTTS", "Home & BTTS Yes"),  # no such bet
        _leg(3, "Liverpool FC", "Chelsea FC", MAN_UTD_SPURS),  # not listed
    ]

    result = ms.MSportConnector(session).create_code(legs)

    assert result.code == "B3T2LV8"
    assert result.unavailable_match_ids == [2, 3]
    assert result.reasons[2] == "MSport Ghana has no bet that settles like HT Result & BTTS: Home & BTTS Yes."
    assert result.reasons[3] == "Not listed on MSport Ghana right now."
    assert 1 not in result.reasons
    assert [s["eventId"] for s in session.posts[0]["json"]["selections"]] == ["sr:match:72221308"]


def test_nothing_bookable_says_so_without_asking_for_a_code():
    session = _Session()

    with pytest.raises(BookingCodeError, match="doesn't list any of these picks"):
        ms.MSportConnector(session).create_code([_leg(1, "Liverpool FC", "Chelsea FC", MAN_UTD_SPURS)])
    assert session.posts == []


def test_the_sites_refusal_reason_is_passed_on():
    session = _Session(share=_Response(payload={"bizCode": 4200, "message": "Selections have expired"}))

    with pytest.raises(BookingCodeError, match="Selections have expired"):
        ms.MSportConnector(session).create_code([_leg(1, "Manchester United FC", "Tottenham Hotspur FC", MAN_UTD_SPURS)])


def _reject_containing(bad_event_id: str, message: str = "invalid event data, no market there"):
    """A share reply that hard-rejects any request touching ``bad_event_id``,
    mirroring what the live API actually does: refuse the whole request,
    with no way to tell from that one reply which selection it didn't like."""

    def respond(selections):
        if any(s["eventId"] == bad_event_id for s in selections):
            return _Response(payload={"bizCode": 4200, "message": message})
        return _Response(payload=_share_reply())

    return respond


def test_one_bad_market_in_a_slip_is_dropped_and_the_rest_still_books():
    session = _Session(share=_reject_containing("sr:match:72478622"))
    legs = [
        _leg(1, "Manchester United FC", "Tottenham Hotspur FC", MAN_UTD_SPURS),
        _leg(2, "Real Madrid", "Villarreal CF", REAL_VILLARREAL, "Total Goals 2.5", "Over 2.5"),
    ]

    result = ms.MSportConnector(session).create_code(legs)

    assert result.code == "B3T2LV8"
    assert result.unavailable_match_ids == [2]
    assert "doesn't offer this exact market" in result.reasons[2]
    assert 1 not in result.reasons
    assert session.posts[-1]["json"]["selections"] == [
        {"eventId": "sr:match:72221308", "marketId": 1, "specifier": "", "outcomeId": "1"},
    ]


def test_a_slip_thats_entirely_bad_still_raises_without_a_code():
    session = _Session(share=lambda selections: _Response(payload={"bizCode": 4200, "message": "no market there"}))
    legs = [
        _leg(1, "Manchester United FC", "Tottenham Hotspur FC", MAN_UTD_SPURS),
        _leg(2, "Real Madrid", "Villarreal CF", REAL_VILLARREAL, "Total Goals 2.5", "Over 2.5"),
    ]

    with pytest.raises(BookingCodeError, match="none of these picks open"):
        ms.MSportConnector(session).create_code(legs)


@pytest.mark.parametrize("session,expected", [
    (_Session(get_error=requests.ConnectionError("refused")), "Couldn't reach MSport Ghana"),
    (_Session(fixtures=_Response(403, {})), "HTTP 403"),
    (_Session(fixtures=_Response(payload=ValueError("not json"))), "can't read"),
    (_Session(share=_Response(451, {})), "HTTP 451"),
])
def test_a_failure_is_reported_as_what_happened(session, expected):
    with pytest.raises(BookingCodeError, match=expected):
        ms.MSportConnector(session).create_code([_leg(1, "Manchester United FC", "Tottenham Hotspur FC", MAN_UTD_SPURS)])


def test_requests_identify_themselves_and_carry_no_browser_identity():
    session = _Session()

    ms.MSportConnector(session).create_code([_leg(1, "Manchester United FC", "Tottenham Hotspur FC", MAN_UTD_SPURS)])

    for sent in (session.gets[0]["headers"], session.posts[0]["headers"]):
        assert sent["User-Agent"] == ms.USER_AGENT
        assert {"operid", "clientid", "platform"} <= set(sent)
        assert not {"Cookie", "Origin", "Referer", "Authorization"} & set(sent)


# --- reuse ---------------------------------------------------------------


def test_the_match_list_is_reused_for_a_few_minutes(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(ms.time, "monotonic", lambda: clock[0])
    session = _Session()
    connector = ms.MSportConnector(session)
    leg = _leg(1, "Manchester United FC", "Tottenham Hotspur FC", MAN_UTD_SPURS)

    connector.create_code([leg])
    connector.create_code([leg])
    assert len(session.gets) == 1

    clock[0] += ms.EVENTS_CACHE_SECONDS + 1
    connector.create_code([leg])
    assert len(session.gets) == 2


# --- registration ------------------------------------------------------------


def test_msport_ghana_is_registered_as_connected():
    assert sites.SITES_BY_KEY["msport_gh"].connected


def test_the_site_step_reports_what_the_code_leaves_out(monkeypatch):
    session = _Session()
    monkeypatch.setattr(ms, "_shared", lambda: (session, ms._EventList(session)))
    legs = [
        _leg(1, "Manchester United FC", "Tottenham Hotspur FC", MAN_UTD_SPURS),
        _leg(2, "Liverpool FC", "Chelsea FC", MAN_UTD_SPURS),
    ]

    code = sites.code_for_site("msport_gh", legs)

    assert code.status == "code_ready" and code.code == "B3T2LV8"
    assert code.unavailable_match_ids == [2]
    assert code.as_json()["unavailable_reasons"] == {"2": "Not listed on MSport Ghana right now."}
    assert "1 of these 2 picks" in code.message
