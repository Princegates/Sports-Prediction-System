"""Free historical + current-season fixture provider backed by the
openfootball/football.json project (https://github.com/openfootball/football.json).

Unlike football-data.co.uk (results only, historical) and TheSportsDB (live
fixture lookup), a single file from this project gives us *both*: every
match in a season, with a ``score`` key present for played matches and
absent for ones still to be played. That means one provider covers both the
historical-import and the upcoming-fixtures job. No signup, no API key --
files are fetched directly from raw.githubusercontent.com.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import requests

BASE_URL = "https://raw.githubusercontent.com/openfootball/football.json/master/{season}/{code}.json"

# openfootball's own league codes.
LEAGUE_FILE_CODES: dict[str, str] = {
    "English Premier League": "en.1",
    "English Championship": "en.2",
    "Spanish La Liga": "es.1",
    "German Bundesliga": "de.1",
    "Italian Serie A": "it.1",
    "French Ligue 1": "fr.1",
    "Dutch Eredivisie": "nl.1",
    "Portuguese Primeira Liga": "pt.1",
}


@dataclass
class OpenFootballMatch:
    league: str
    season: str
    date: dt.datetime
    home_team: str
    away_team: str
    home_score: int | None
    away_score: int | None
    ht_home_score: int | None
    ht_away_score: int | None

    @property
    def is_played(self) -> bool:
        return self.home_score is not None


def fetch_season(league_name: str, season: str, timeout: int = 30) -> list[OpenFootballMatch]:
    """``season`` is openfootball's own folder naming, e.g. "2025-26"."""

    code = LEAGUE_FILE_CODES.get(league_name)
    if code is None:
        raise ValueError(f"No openfootball league code known for {league_name!r}")

    url = BASE_URL.format(season=season, code=code)
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    payload = response.json()

    matches: list[OpenFootballMatch] = []
    for raw in payload.get("matches", []):
        date_str = raw.get("date")
        if not date_str:
            continue
        time_str = raw.get("time") or "15:00"
        try:
            when = dt.datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        except ValueError:
            when = dt.datetime.strptime(date_str, "%Y-%m-%d")

        score = raw.get("score")
        if isinstance(score, dict):
            ft = score.get("ft")
            ht = score.get("ht")
        elif isinstance(score, list):
            # A handful of entries record only the final score as a bare
            # [home, away] pair, with no half-time breakdown.
            ft = score
            ht = None
        else:
            ft = None
            ht = None

        matches.append(
            OpenFootballMatch(
                league=league_name,
                season=season,
                date=when,
                home_team=raw["team1"],
                away_team=raw["team2"],
                home_score=ft[0] if ft else None,
                away_score=ft[1] if ft else None,
                ht_home_score=ht[0] if ht else None,
                ht_away_score=ht[1] if ht else None,
            )
        )
    return matches
