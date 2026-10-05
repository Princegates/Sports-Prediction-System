"""Football.com Ghana: a thin relabelling of SportyBet's own connector, not
a separate API -- see app/betcode/football_gh.py's docstring for why. These
tests check the relabelling itself; the booking and matching logic is
SportyBet's own and is covered by test_sportybet_connector.py.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.betcode import football_gh as fg
from app.betcode import sites
from app.betcode.selection import Leg
from app.betcode.sites import BookingCodeError

MAN_UTD_SPURS = dt.datetime(2026, 10, 10, 16, 30)
REAL_VILLARREAL = dt.datetime(2026, 10, 10, 19, 0)


def _ms(when: dt.datetime) -> int:
    return int(when.replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def _event(event_id: str, home: str, away: str, kickoff: dt.datetime) -> dict:
    return {
        "eventId": event_id, "gameId": "1", "estimateStartTime": _ms(kickoff), "status": 0,
        "matchStatus": "Not start", "homeTeamName": home, "awayTeamName": away, "markets": [],
        "bookingStatus": "Booked",
    }


PAGE_ONE = {
    "bizCode": 10000, "message": "0#0",
    "data": {"totalNum": 1, "tournaments": [
        {"id": "sr:tournament:17", "name": "sr:tournament:17", "events": [
            _event("sr:match:72221308", "Man Utd", "Tottenham", MAN_UTD_SPURS),
        ]},
    ]},
}


def _share_reply(code: str = "9Y8YN0") -> dict:
    return {
        "bizCode": 10000, "isAvailable": True, "message": "Success",
        "data": {
            "shareCode": code, "shareURL": f"http://www.sportybet.com/gh/?shareCode={code}",
            "ticket": {"selections": []}, "deadline": 1791678600000, "outcomes": [],
            "unavailableOutcomes": [],
        },
    }


class _Response:
    def __init__(self, status_code: int = 200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _Session:
    def __init__(self, share=None):
        self.share = share if share is not None else _Response(payload=_share_reply())
        self.posts: list[dict] = []

    def get(self, url, params=None, headers=None, timeout=None):
        return _Response(payload=PAGE_ONE)

    def post(self, url, json=None, headers=None, timeout=None):
        self.posts.append({"url": url, "json": json, "headers": headers})
        return self.share


def _leg(match_id: int, home: str, away: str, kickoff: dt.datetime, market="Match Result", selection="Home Win") -> Leg:
    return Leg(match_id=match_id, league="Any", home_team=home, away_team=away, kickoff=kickoff,
               market=market, selection=selection, model_probability=0.6)


def test_a_booked_slip_carries_the_code_but_no_sportybet_link():
    session = _Session()
    result = fg.FootballComGhConnector(session).create_code(
        [_leg(1, "Manchester United FC", "Tottenham Hotspur FC", MAN_UTD_SPURS)]
    )

    assert result.code == "9Y8YN0"
    assert result.link is None
    # It still talks to SportyBet's own endpoint -- that's the whole point.
    from app.betcode import sportybet as sb
    assert session.posts[0]["url"] == sb.SHARE_URL


def test_a_leg_not_found_is_explained_as_football_com_not_sportybet():
    session = _Session()
    result = fg.FootballComGhConnector(session).create_code([
        _leg(1, "Manchester United FC", "Tottenham Hotspur FC", MAN_UTD_SPURS),
        _leg(2, "Liverpool FC", "Chelsea FC", MAN_UTD_SPURS),  # not in PAGE_ONE
    ])

    assert result.reasons[2] == "Not listed on Football.com Ghana right now."
    assert "SportyBet" not in result.reasons[2]


def test_a_site_refusal_is_relabelled_too():
    session = _Session(share=_Response(payload={"bizCode": 4200, "message": "Selections have expired"}))

    with pytest.raises(BookingCodeError) as exc_info:
        fg.FootballComGhConnector(session).create_code(
            [_leg(1, "Manchester United FC", "Tottenham Hotspur FC", MAN_UTD_SPURS)]
        )

    assert "Football.com Ghana wouldn't book this slip" in str(exc_info.value)
    assert "SportyBet" not in str(exc_info.value)


def test_football_com_ghana_is_registered_as_connected():
    assert sites.SITES_BY_KEY["football_gh"].connected


def test_the_site_step_uses_the_relabelled_connector(monkeypatch):
    session = _Session()
    import app.betcode.sportybet as sb
    monkeypatch.setattr(sb, "_shared", lambda: (session, sb._EventList(session)))

    code = sites.code_for_site("football_gh", [_leg(1, "Manchester United FC", "Tottenham Hotspur FC", MAN_UTD_SPURS)])

    assert code.status == "code_ready" and code.code == "9Y8YN0"
    assert code.link is None
