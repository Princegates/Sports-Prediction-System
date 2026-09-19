"""Free historical match-result provider.

football-data.co.uk publishes CSV files of results (plus closing odds, which
we deliberately never load into the prediction pipeline -- this system is
market-independent per the SRS) for the major European leagues going back to
the 1990s. No signup, no API key, no rate limit beyond "don't hammer it".

CSV columns of interest (season format varies slightly by era but these are
stable since the mid-2000s):
    Date, HomeTeam, AwayTeam, FTHG, FTAG, FTR, HTHG, HTAG, HTR
"""

from __future__ import annotations

import datetime as dt
import io
from dataclasses import dataclass

import pandas as pd
import requests

BASE_URL = "https://www.football-data.co.uk/mmz4281/{season}/{league}.csv"

# A subset of the league codes football-data.co.uk uses. Extend freely --
# the full list is at https://www.football-data.co.uk/notes.txt
LEAGUE_CODES: dict[str, str] = {
    "E0": "English Premier League",
    "E1": "English Championship",
    "SP1": "Spanish La Liga",
    "D1": "German Bundesliga",
    "I1": "Italian Serie A",
    "F1": "French Ligue 1",
    "N1": "Dutch Eredivisie",
    "P1": "Portuguese Primeira Liga",
    "SC0": "Scottish Premiership",
    "T1": "Turkish Süper Lig",
}


@dataclass
class RawMatch:
    league_code: str
    season: str
    date: dt.datetime
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    ht_home_score: int | None
    ht_away_score: int | None

    # Present from roughly 2000 onward, absent in older files and
    # occasionally blank for an individual match.
    referee: str | None = None
    home_shots: int | None = None
    away_shots: int | None = None
    home_shots_on_target: int | None = None
    away_shots_on_target: int | None = None
    home_corners: int | None = None
    away_corners: int | None = None
    home_fouls: int | None = None
    away_fouls: int | None = None
    home_yellows: int | None = None
    away_yellows: int | None = None
    home_reds: int | None = None
    away_reds: int | None = None


def _parse_date(value: str) -> dt.datetime:
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return dt.datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise ValueError(f"Unrecognized date format: {value!r}")


def _opt_int(row, column: str) -> int | None:
    """Read an integer stat that may be missing from the file entirely, blank
    for this match, or non-numeric. All three are normal in older seasons and
    none should abort the import."""

    if column not in row or pd.isna(row[column]):
        return None
    try:
        return int(float(row[column]))
    except (TypeError, ValueError):
        return None


def _opt_str(row, column: str) -> str | None:
    if column not in row or pd.isna(row[column]):
        return None
    value = str(row[column]).strip()
    return value or None


def fetch_season(league_code: str, season: str, timeout: int = 30) -> list[RawMatch]:
    """Download and parse one season's CSV.

    ``season`` uses football-data.co.uk's own 4-digit convention, e.g.
    "2324" for the 2023-24 season.
    """

    url = BASE_URL.format(season=season, league=league_code)
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    # Some files are latin-1 encoded.
    text = response.content.decode("utf-8", errors="replace")
    df = pd.read_csv(io.StringIO(text))
    df = df.dropna(subset=["HomeTeam", "AwayTeam", "FTHG", "FTAG"])

    matches: list[RawMatch] = []
    for _, row in df.iterrows():
        try:
            date = _parse_date(str(row["Date"]))
        except ValueError:
            continue
        matches.append(
            RawMatch(
                league_code=league_code,
                season=season,
                date=date,
                home_team=str(row["HomeTeam"]).strip(),
                away_team=str(row["AwayTeam"]).strip(),
                home_score=int(row["FTHG"]),
                away_score=int(row["FTAG"]),
                ht_home_score=_opt_int(row, "HTHG"),
                ht_away_score=_opt_int(row, "HTAG"),
                referee=_opt_str(row, "Referee"),
                home_shots=_opt_int(row, "HS"),
                away_shots=_opt_int(row, "AS"),
                home_shots_on_target=_opt_int(row, "HST"),
                away_shots_on_target=_opt_int(row, "AST"),
                home_corners=_opt_int(row, "HC"),
                away_corners=_opt_int(row, "AC"),
                home_fouls=_opt_int(row, "HF"),
                away_fouls=_opt_int(row, "AF"),
                home_yellows=_opt_int(row, "HY"),
                away_yellows=_opt_int(row, "AY"),
                home_reds=_opt_int(row, "HR"),
                away_reds=_opt_int(row, "AR"),
            )
        )
    return matches
