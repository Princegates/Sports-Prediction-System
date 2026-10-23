"""Team news for a match -- injuries and confirmed lineups -- from API-Football.

Two requests per match at most (``/injuries`` and ``/fixtures/lineups``), made
on demand when someone opens the match and cached in ``MatchTeamNews`` so a
hundred views cost one request, not a hundred. The key's daily budget is
shared with the fixture import and the live poll, so this feature is held to a
fifth of it and backs off further when the provider says little is left; past
either limit it serves what it has cached, or says it is unavailable. It never
fails the page and never estimates a missing answer.

How long a cached answer stays fresh:

* injuries -- six hours while kickoff is more than a day off, one hour inside
  it (squads change most on matchday);
* lineups -- only asked for from ``LINEUPS_WINDOW`` before kickoff, when clubs
  start submitting them; an empty answer ("not out yet") is re-asked every five
  minutes, a real one every half hour until kickoff and kept for good after it.

Nothing is fetched for a match that kicked off more than ``AFTER_FULL_TIME`` ago
and was never cached: team news for a finished match is no longer news.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import app_settings
from app.data.providers.api_football import ApiFootballClient, ApiFootballError, QuotaExceeded
from app.data.team_matching import normalize_team_name
from app.db.models import Match, MatchTeamNews
from app.timeutil import utcnow

logger = logging.getLogger(__name__)

INJURIES_TTL_FAR = dt.timedelta(hours=6)
INJURIES_TTL_NEAR = dt.timedelta(hours=1)
NEAR = dt.timedelta(hours=24)
LINEUPS_WINDOW = dt.timedelta(minutes=90)
LINEUPS_RETRY = dt.timedelta(minutes=5)
LINEUPS_TTL = dt.timedelta(minutes=30)
AFTER_FULL_TIME = dt.timedelta(hours=6)
# After a failed request, leave the provider alone this long (per worker).
FAILURE_BACKOFF = 600.0
# This feature's share of the daily budget, and the headroom it leaves the
# daily import when the provider reports how much is left.
DAILY_SHARE = 5  # one fifth
RESERVE_REMAINING = 600

_last_failure: dict[tuple[int, str], float] = {}
_reported_remaining: int | None = None


def _client(db: Session) -> tuple[ApiFootballClient | None, int]:
    """A client built from the stored settings, and this feature's daily call
    cap; ``(None, 0)`` when no key is configured."""

    values = app_settings.all_values(db)
    key = str(values.get("api_football_key") or "")
    if not key:
        return None, 0
    budget = int(values.get("api_football_daily_budget") or 7500)
    client = ApiFootballClient(
        key,
        host=str(values.get("api_football_host") or "v3.football.api-sports.io"),
        daily_budget=budget,
        per_minute=int(values.get("api_football_per_minute") or 300),
        timeout=10.0,
    )
    return client, budget // DAILY_SHARE


def _calls_today(db: Session, today: dt.date) -> int:
    return int(db.execute(select(func.coalesce(func.sum(MatchTeamNews.calls_made), 0)).where(
        MatchTeamNews.calls_day == today
    )).scalar_one())


def _row(db: Session, match: Match) -> MatchTeamNews:
    row = db.execute(select(MatchTeamNews).where(MatchTeamNews.match_id == match.id)).scalar_one_or_none()
    if row is None:
        row = MatchTeamNews(match_id=match.id, calls_made=0)
        db.add(row)
        db.flush()
    return row


def _may_fetch(db: Session, match: Match, kind: str, now: dt.datetime) -> tuple[ApiFootballClient | None, str | None]:
    """A client if a request is allowed right now, else why not."""

    if match.api_fixture_id is None:
        return None, "This match isn't linked to the data provider, so there's no team news for it."
    client, cap = _client(db)
    if client is None:
        return None, "Team news isn't switched on (no data-provider key is configured)."
    if _reported_remaining is not None and _reported_remaining < RESERVE_REMAINING:
        return None, "Team news is paused for today to leave room for the daily match updates."
    if _calls_today(db, now.date()) >= cap:
        return None, "Team news has reached today's request limit; it will be back tomorrow."
    if time.monotonic() - _last_failure.get((match.id, kind), -FAILURE_BACKOFF) < FAILURE_BACKOFF:
        return None, "The data provider didn't answer just now; it will be tried again shortly."
    return client, None


def _request(db: Session, match: Match, row: MatchTeamNews, kind: str, client: ApiFootballClient, now: dt.datetime):
    """Make one request, counting it against today's cap first. Returns the
    provider's rows, or ``None`` if it failed."""

    global _reported_remaining
    if row.calls_day != now.date():
        row.calls_day, row.calls_made = now.date(), 0
    row.calls_made += 1
    db.commit()  # counted even if the request then fails -- it costs quota either way
    try:
        rows = client.injuries(match.api_fixture_id) if kind == "injuries" else client.lineups(match.api_fixture_id)
    except QuotaExceeded:
        _last_failure[(match.id, kind)] = time.monotonic()
        return None
    except ApiFootballError as exc:
        logger.warning("team news %s for match %s failed: %s", kind, match.id, exc)
        _last_failure[(match.id, kind)] = time.monotonic()
        return None
    if client.quota.remaining_reported is not None:
        _reported_remaining = client.quota.remaining_reported
    return rows


def _side(match: Match, team: dict[str, Any]) -> str | None:
    """"home"/"away" for a provider team block: by its API-Football id, else by
    name, else neither (the row is dropped rather than guessed onto a side)."""

    team_id = team.get("id")
    if team_id is not None:
        if team_id == match.home_team.api_football_team_id:
            return "home"
        if team_id == match.away_team.api_football_team_id:
            return "away"
    name = normalize_team_name(str(team.get("name") or ""))
    if name and name == normalize_team_name(match.home_team.name):
        return "home"
    if name and name == normalize_team_name(match.away_team.name):
        return "away"
    return None


def _iso(when: dt.datetime | None) -> str | None:
    return when.isoformat() if when else None


# --- injuries -------------------------------------------------------------


def _injuries_ttl(match: Match, now: dt.datetime) -> dt.timedelta:
    return INJURIES_TTL_NEAR if match.date - now <= NEAR else INJURIES_TTL_FAR


def _trim_injuries(rows: list[dict]) -> list[dict]:
    out, seen = [], set()
    for r in rows:
        player, team = r.get("player") or {}, r.get("team") or {}
        name = player.get("name")
        if not name or (player.get("id"), team.get("id")) in seen:
            continue
        seen.add((player.get("id"), team.get("id")))
        out.append({
            "team_id": team.get("id"), "team_name": team.get("name"),
            "name": name, "type": player.get("type"), "reason": player.get("reason"),
        })
    return out


def injuries_for(db: Session, match: Match, now: dt.datetime | None = None) -> dict:
    """``{"status", "reason", "home", "away", "updated_at"}`` -- the players out
    or doubtful for each side. ``status`` is "ok" (possibly with empty lists:
    no injuries reported) or "unavailable" with a ``reason``."""

    now = now or utcnow()
    row = db.execute(select(MatchTeamNews).where(MatchTeamNews.match_id == match.id)).scalar_one_or_none()
    cached = row is not None and row.injuries is not None
    fresh = cached and now - row.injuries_fetched_at < _injuries_ttl(match, now)
    reason: str | None = None

    if not fresh:
        if not cached and now - match.date > AFTER_FULL_TIME:
            return _unavailable("Team news isn't kept for matches that have finished.")
        client, reason = _may_fetch(db, match, "injuries", now)
        if client is not None:
            row = row or _row(db, match)
            rows = _request(db, match, row, "injuries", client, now)
            if rows is not None:
                row.injuries, row.injuries_fetched_at = _trim_injuries(rows), now
            db.commit()
            cached = row.injuries is not None
            reason = None if rows is not None else "The data provider didn't answer just now."

    if not cached:
        return _unavailable(reason or "No team news yet.")
    sides: dict[str, list[dict]] = {"home": [], "away": []}
    for item in row.injuries:
        side = _side(match, {"id": item.get("team_id"), "name": item.get("team_name")})
        if side:
            sides[side].append({"name": item["name"], "type": item.get("type"), "reason": item.get("reason")})
    return {"status": "ok", "reason": None, **sides, "updated_at": _iso(row.injuries_fetched_at)}


# --- lineups --------------------------------------------------------------


def _trim_lineups(rows: list[dict]) -> dict:
    def players(block: list[dict] | None) -> list[dict]:
        return [
            {"name": p.get("name"), "number": p.get("number"), "pos": p.get("pos")}
            for p in ((entry or {}).get("player") or {} for entry in (block or []))
            if p.get("name")
        ]

    return {"teams": [
        {
            "team_id": (r.get("team") or {}).get("id"),
            "team_name": (r.get("team") or {}).get("name"),
            "formation": r.get("formation"),
            "coach": (r.get("coach") or {}).get("name"),
            "start_xi": players(r.get("startXI")),
            "substitutes": players(r.get("substitutes")),
        }
        for r in rows
    ]} if rows else {}


def _lineups_fresh(row: MatchTeamNews | None, match: Match, now: dt.datetime) -> bool:
    if row is None or row.lineups is None or row.lineups_fetched_at is None:
        return False
    if not row.lineups:  # asked, and they were not out yet
        return now - row.lineups_fetched_at < LINEUPS_RETRY
    if now >= match.date:  # published and the match has started: it cannot change
        return True
    return now - row.lineups_fetched_at < LINEUPS_TTL


def lineups_for(db: Session, match: Match, now: dt.datetime | None = None) -> dict:
    """``{"status", "reason", "home", "away", "updated_at"}``. ``status`` is "ok"
    with each side's formation, coach, starting XI and bench; "pending" before
    the clubs have submitted them; or "unavailable" with a ``reason``."""

    now = now or utcnow()
    row = db.execute(select(MatchTeamNews).where(MatchTeamNews.match_id == match.id)).scalar_one_or_none()
    reason: str | None = None

    if not _lineups_fresh(row, match, now):
        if match.date - now > LINEUPS_WINDOW:
            return {"status": "pending", "reason": "Confirmed lineups usually appear about an hour before kickoff.",
                    "home": None, "away": None, "updated_at": None}
        if not (row is not None and row.lineups) and now - match.date > AFTER_FULL_TIME:
            return _unavailable("Lineups aren't kept for matches that have finished.", lineups=True)
        client, reason = _may_fetch(db, match, "lineups", now)
        if client is not None:
            row = row or _row(db, match)
            rows = _request(db, match, row, "lineups", client, now)
            if rows is not None:
                row.lineups, row.lineups_fetched_at = _trim_lineups(rows), now
            db.commit()
            reason = None if rows is not None else "The data provider didn't answer just now."

    if row is None or not row.lineups:
        if reason:
            return _unavailable(reason, lineups=True)
        return {"status": "pending", "reason": "The clubs haven't submitted their lineups yet.",
                "home": None, "away": None, "updated_at": _iso(row.lineups_fetched_at) if row else None}

    sides: dict[str, dict | None] = {"home": None, "away": None}
    for team in row.lineups.get("teams", []):
        side = _side(match, {"id": team.get("team_id"), "name": team.get("team_name")})
        if side:
            sides[side] = {k: team.get(k) for k in ("formation", "coach", "start_xi", "substitutes")}
    return {"status": "ok", "reason": None, **sides, "updated_at": _iso(row.lineups_fetched_at)}


def _unavailable(reason: str, *, lineups: bool = False) -> dict:
    empty: Any = None if lineups else []
    return {"status": "unavailable", "reason": reason, "home": empty, "away": empty, "updated_at": None}
