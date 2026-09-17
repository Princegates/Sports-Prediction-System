"""Upcoming-fixtures provider -- currently non-functional on TheSportsDB's
free tier, kept for reference / in case they loosen this again.

As of this writing, TheSportsDB's shared free key ("123", after they
rotated away from the old "3") caps every league-listing method
(``search_all_leagues.php``, ``all_leagues.php``) at a handful of results
regardless of the ``s=Soccer`` filter -- confirmed by hand, not a bug in
this code. ``_find_league_id`` below can therefore only ever find a league
that happens to land in that small capped page, which in practice means it
finds almost nothing. A personal (non-shared) key exists but is a
paid-Patreon perk on their site, which this project deliberately avoids.

Prefer ``openfootball.py`` for anything it covers. This module is left in
place because TheSportsDB's limits have changed before and may loosen
again, and because it's still a reasonable pattern to adapt if you do have
a personal/paid key.
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


def fetch_upcoming_fixtures(league_name: str, api_key: str = "123", timeout: int = 15) -> list[UpcomingFixture]:
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
