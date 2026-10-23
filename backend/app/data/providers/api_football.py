"""API-Football (api-sports.io) client, built around a request budget.

The plan sets the ceiling -- 100 a day on the free tier, 7,500 on Pro -- and
the defaults here match Pro, with both limits overridable from the settings
panel. Whatever the number, it is finite and shared with every other job using
the key, so the budget is part of the design rather than a note in the README:
one careless loop over a week of fixtures spends the allowance before lunch
and the site silently stops updating.

So the limits live here, in code. ``QuotaExceeded`` is raised before a request
that would breach the daily budget rather than after the API refuses it, and
calls are spaced to respect the per-minute cap. Callers get an exception they
can catch and degrade from -- never a silent partial result, which is the
failure mode that would quietly corrupt a day's predictions.

This is the project's only fixture and result source. It started as a
supplement to openfootball's free season files, but running both stored every
match twice under two spellings of each club (see app/data/source_cleanup.py),
so the daily refresh now imports from here alone.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

DIRECT_HOST = "v3.football.api-sports.io"

# API-Football's own league ids. Fixed by the provider, not by us.
LEAGUE_IDS: dict[str, int] = {
    "English Premier League": 39,
    "Spanish La Liga": 140,
    "Italian Serie A": 135,
    "German Bundesliga": 78,
    "French Ligue 1": 61,
    "Dutch Eredivisie": 88,
    "Portuguese Primeira Liga": 94,
    "Turkish Süper Lig": 203,
    "UEFA Champions League": 2,
    "UEFA Europa League": 3,
    # England's second tier: offered to members like the leagues above, so
    # it's imported daily with them (it used to sit in the database from an
    # old free feed, predicted but never refreshed or listed in the app).
    "English Championship": 40,
    # Below: domestic leagues added so every Champions League entrant's home
    # league has a real Team row to resolve against, instead of its fixture
    # being skipped (see app/data/api_football_ingest.py's module
    # docstring). Found by scripts/map_cl_unresolved_leagues.py against the
    # current Champions League league-phase + qualifying rounds -- every
    # club API-Football returned for it that this project didn't already
    # hold. Full parity with the leagues above: fixture import, Elo
    # rebuild, gradient-boosting training and backtest (scripts/
    # backtest.py), and listed in app/prediction_models/ml_model.py's
    # KNOWN_LEAGUES.
    "Albanian Superliga": 310,
    "Andorran Primera Divisió": 312,
    "Armenian Premier League": 342,
    "Austrian Bundesliga": 218,
    "Azerbaijan Premyer Liqa": 419,
    "Belarusian Premier League": 116,
    "Belgian Pro League": 144,
    "Bosnian Premijer Liga": 315,
    "Bulgarian First League": 172,
    "Croatian HNL": 210,
    "Cypriot First Division": 318,
    "Czech First League": 345,
    "Danish Superliga": 119,
    "Estonian Meistriliiga": 329,
    "Faroese Meistaradeildin": 367,
    "Finnish Veikkausliiga": 244,
    "Georgian Erovnuli Liga": 327,
    "Gibraltar Premier Division": 758,
    "Greek Super League": 197,
    "Hungarian NB I": 271,
    "Icelandic Úrvalsdeild": 164,
    "League of Ireland Premier Division": 357,
    "Israeli Ligat Ha'al": 383,
    "Kazakh Premier League": 389,
    "Kosovan Superliga": 664,
    "Latvian Virslīga": 365,
    "Lithuanian A Lyga": 362,
    "Luxembourg National Division": 261,
    "Macedonian First League": 371,
    "Maltese Premier League": 393,
    "Moldovan Super Liga": 394,
    "Montenegrin First League": 355,
    "NIFL Premiership": 408,
    "Norwegian Eliteserien": 103,
    "Polish Ekstraklasa": 106,
    "Romanian Liga I": 283,
    "San Marino Campionato": 404,
    "Scottish Premiership": 179,
    "Serbian SuperLiga": 286,
    "Slovak Super Liga": 332,
    "Slovenian PrvaLiga": 373,
    "Swedish Allsvenskan": 113,
    "Swiss Super League": 207,
    "Ukrainian Premier League": 333,
    "Welsh Premier League": 110,
}
ID_TO_LEAGUE = {v: k for k, v in LEAGUE_IDS.items()}


class ApiFootballError(RuntimeError):
    """The API answered, but not with what was asked for."""


class QuotaExceeded(ApiFootballError):
    """Raised *before* spending a request that would breach the daily budget.

    Deliberately not a soft failure. A caller that swallows this and carries on
    with partial data produces a day of predictions built on half a fixture
    list, which looks fine and is wrong.
    """


@dataclass
class Quota:
    """What has been spent, and what the API last said was left.

    ``limit_per_day`` is what we enforce; ``remaining_reported`` is what the
    API's own headers claim. They can disagree -- other processes share the
    key -- so the lower of the two governs.
    """

    limit_per_day: int = 7500
    limit_per_minute: int = 300
    used_this_run: int = 0
    remaining_reported: int | None = None
    _minute_window: list[float] = field(default_factory=list)

    def remaining(self) -> int:
        ours = self.limit_per_day - self.used_this_run
        if self.remaining_reported is None:
            return ours
        return min(ours, self.remaining_reported)


class ApiFootballClient:
    def __init__(
        self,
        api_key: str,
        *,
        host: str = DIRECT_HOST,
        daily_budget: int = 7500,
        per_minute: int = 300,
        timeout: float = 30.0,
    ) -> None:
        if not api_key:
            raise ApiFootballError("An API-Football key is required.")
        self._key = api_key
        self.host = host
        self.timeout = timeout
        self.quota = Quota(limit_per_day=daily_budget, limit_per_minute=per_minute)

    # -- plumbing --------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        # The same product is sold direct and through RapidAPI with different
        # auth headers. Derived from the host so there is no third setting to
        # get wrong.
        if "rapidapi" in self.host:
            return {"x-rapidapi-key": self._key, "x-rapidapi-host": self.host}
        return {"x-apisports-key": self._key}

    def _respect_per_minute(self) -> None:
        now = time.monotonic()
        window = [t for t in self.quota._minute_window if now - t < 60]
        if len(window) >= self.quota.limit_per_minute:
            sleep_for = 60 - (now - window[0]) + 0.5
            logger.info("API-Football per-minute cap reached; pausing %.1fs", sleep_for)
            time.sleep(max(0.0, sleep_for))
            now = time.monotonic()
            window = [t for t in window if now - t < 60]
        window.append(now)
        self.quota._minute_window = window

    def get(self, path: str, params: dict[str, object] | None = None) -> list[dict]:
        rows, _ = self.get_page(path, params)
        return rows

    def get_page(self, path: str, params: dict[str, object] | None = None) -> tuple[list[dict], dict]:
        """Rows plus the paging block.

        Odds come back paged, and a fetch that spans several days of quota has
        to know which page it stopped on. Discarding that -- which the plain
        ``get`` does -- means restarting from the beginning every time and
        never finishing a season.
        """

        if self.quota.remaining() <= 0:
            raise QuotaExceeded(
                f"Daily API-Football budget spent ({self.quota.used_this_run} used). "
                "Nothing was requested; try again after the quota resets."
            )

        self._respect_per_minute()

        query = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None})
        url = f"https://{self.host}/{path}" + (f"?{query}" if query else "")
        request = urllib.request.Request(url, headers=self._headers())

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode())
                headers = dict(response.headers)
        except urllib.error.HTTPError as exc:
            raise ApiFootballError(f"HTTP {exc.code} from {path}") from exc
        except Exception as exc:  # noqa: BLE001
            raise ApiFootballError(f"{type(exc).__name__} calling {path}: {exc}") from exc
        finally:
            # Counted even on failure: a rejected request still costs quota.
            self.quota.used_this_run += 1

        left = headers.get("x-ratelimit-requests-remaining")
        if left is not None:
            try:
                self.quota.remaining_reported = int(left)
            except ValueError:
                pass

        # API-Football reports failures in the body with a 200 status. An
        # empty list here is a legitimate "nothing found"; a dict is an error.
        errors = payload.get("errors")
        if errors and not isinstance(errors, list):
            raise ApiFootballError(f"{path}: {errors}")

        return payload.get("response") or [], payload.get("paging") or {}

    # -- endpoints -------------------------------------------------------

    def status(self) -> dict:
        rows = self.get("status")
        return rows if isinstance(rows, dict) else (rows[0] if rows else {})

    def teams(self, *, league_id: int, season: int) -> list[dict]:
        """Every club in one league-season -- one request. Each row carries
        ``team.id`` and ``team.name`` (and the crest URL, which this project
        derives from the id instead of storing)."""

        return self.get("teams", {"league": league_id, "season": season})

    def fixtures(
        self,
        *,
        league_id: int | None = None,
        season: int | None = None,
        date: dt.date | None = None,
        from_date: dt.date | None = None,
        to_date: dt.date | None = None,
    ) -> list[dict]:
        """Fixtures, one call per filter combination.

        A date range covering several leagues is one request; asking per league
        is one each. Prefer the range when the budget matters.
        """

        return self.get(
            "fixtures",
            {
                "league": league_id,
                "season": season,
                "date": date.isoformat() if date else None,
                "from": from_date.isoformat() if from_date else None,
                "to": to_date.isoformat() if to_date else None,
            },
        )

    def live_fixtures(self) -> list[dict]:
        """Every fixture in play anywhere, right now -- one request regardless
        of how many matches that is, unlike ``fixtures()`` which is scoped to
        a single league and season. Callers filter down to the leagues this
        project tracks; most of the world's live board is not one of them."""

        return self.get("fixtures", {"live": "all"})

    def fixture_by_id(self, fixture_id: int) -> dict | None:
        """One fixture's current row, straight from the provider -- for a
        match our own DB still has marked LIVE that this poll's
        ``live_fixtures()`` didn't return at all (it finished and dropped off
        the live board before any poll caught it mid-FT), not something the
        day-to-day sync path needs otherwise. ``None`` if the provider has
        nothing under this id (removed/postponed fixture)."""

        rows = self.get("fixtures", {"id": fixture_id})
        return rows[0] if rows else None

    def injuries(self, fixture_id: int) -> list[dict]:
        """Players ruled out or doubtful for one fixture -- one request.
        Each row has ``player`` (name, ``type`` "Missing Fixture" or
        "Questionable", ``reason``) and ``team`` (id, name)."""

        return self.get("injuries", {"fixture": fixture_id})

    def lineups(self, fixture_id: int) -> list[dict]:
        """Both teams' formations, starting XIs and benches for one fixture --
        one request. Empty until the clubs submit them, roughly an hour
        before kickoff."""

        return self.get("fixtures/lineups", {"fixture": fixture_id})

    def odds(self, *, fixture_id: int | None = None, league_id: int | None = None,
             season: int | None = None, date: dt.date | None = None, page: int = 1) -> list[dict]:
        return self.get(
            "odds",
            {
                "fixture": fixture_id,
                "league": league_id,
                "season": season,
                "date": date.isoformat() if date else None,
                "page": page,
            },
        )

    def lineups(self, fixture_id: int) -> list[dict]:
        return self.get("fixtures/lineups", {"fixture": fixture_id})

    def injuries(self, *, league_id: int, season: int) -> list[dict]:
        return self.get("injuries", {"league": league_id, "season": season})
