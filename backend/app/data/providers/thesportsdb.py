"""Free upcoming-fixtures provider.

TheSportsDB (https://www.thesportsdb.com/api.php) offers a public test key
("3") that is free forever and requires no signup. It is rate-limited and
not meant for high-volume production use, but it is more than enough to
discover a league's next fixtures for a demo/dev deployment. Swap
``api_key`` for your own free key (still no cost) if you outgrow the shared
test key's rate limit.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import requests

BASE_URL = "https://www.thesportsdb.com/api/v1/json/{key}"


@dataclass
class UpcomingFixture:
    home_team: str
    away_team: str
    date: dt.datetime
    league: str
    external_id: str


def _find_league_id(league_name: str, api_key: str, timeout: int = 15) -> str | None:
    url = f"{BASE_URL.format(key=api_key)}/search_all_leagues.php"
    response = requests.get(url, params={"s": "Soccer"}, timeout=timeout)
    response.raise_for_status()
    payload = response.json() or {}
    for league in payload.get("countries") or []:
        if league.get("strLeague", "").lower() == league_name.lower():
            return league.get("idLeague")
    return None


def fetch_upcoming_fixtures(league_name: str, api_key: str = "3", timeout: int = 15) -> list[UpcomingFixture]:
    league_id = _find_league_id(league_name, api_key, timeout=timeout)
    if league_id is None:
        return []

    url = f"{BASE_URL.format(key=api_key)}/eventsnextleague.php"
    response = requests.get(url, params={"id": league_id}, timeout=timeout)
    response.raise_for_status()
    payload = response.json() or {}

    fixtures: list[UpcomingFixture] = []
    for event in payload.get("events") or []:
        date_str = event.get("dateEvent")
        time_str = event.get("strTime") or "00:00:00"
        if not date_str:
            continue
        try:
            when = dt.datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        home = event.get("strHomeTeam")
        away = event.get("strAwayTeam")
        if not home or not away:
            continue
        fixtures.append(
            UpcomingFixture(
                home_team=home,
                away_team=away,
                date=when,
                league=league_name,
                external_id=str(event.get("idEvent")),
            )
        )
    return fixtures
