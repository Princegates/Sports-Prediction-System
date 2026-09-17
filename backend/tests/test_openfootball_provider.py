import json
from unittest.mock import patch

from app.data.providers import openfootball as ofb


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def test_fetch_season_handles_dict_and_list_and_missing_scores():
    payload = {
        "matches": [
            {"date": "2025-08-15", "time": "20:00", "team1": "A", "team2": "B", "score": {"ft": [4, 2], "ht": [1, 0]}},
            {"date": "2025-08-16", "time": "12:30", "team1": "C", "team2": "D", "score": [0, 0]},
            {"date": "2025-09-18", "time": "15:00", "team1": "E", "team2": "F"},  # unplayed: no score key at all
        ]
    }

    with patch("app.data.providers.openfootball.requests.get", return_value=_FakeResponse(payload)):
        matches = ofb.fetch_season("English Premier League", "2025-26")

    assert len(matches) == 3

    played_with_ht = matches[0]
    assert played_with_ht.is_played
    assert (played_with_ht.home_score, played_with_ht.away_score) == (4, 2)
    assert (played_with_ht.ht_home_score, played_with_ht.ht_away_score) == (1, 0)

    played_list_only = matches[1]
    assert played_list_only.is_played
    assert (played_list_only.home_score, played_list_only.away_score) == (0, 0)
    assert played_list_only.ht_home_score is None

    unplayed = matches[2]
    assert not unplayed.is_played
    assert unplayed.home_score is None and unplayed.away_score is None
