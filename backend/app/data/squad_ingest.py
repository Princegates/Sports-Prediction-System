"""Bringing squad availability -- injuries, suspensions, confirmed lineups --
into this project's own tables.

Two jobs, on two different clocks. Injuries and suspensions are known days
ahead and barely change hour to hour, so they're fetched once per league as
part of the daily refresh (see ``import_injuries``/``import_injuries_from_settings``,
wired into the same cadence as fixtures). A confirmed starting lineup isn't
known until roughly 60-75 minutes before kickoff, which is far too late for a
once-a-day batch -- that half lives in the near-kickoff poll
(``import_lineup``), called from the same in-process loop that already polls
live scores every few minutes (see app.main._live_sync_loop).

Team identity here reuses app.data.api_football_ingest.TeamIndex -- the same
fuzzy name matching the fixture importer already relies on, since this
project stores no provider id for a team, only for players (see Player's own
docstring for why that's different).
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data.api_football_ingest import TeamIndex
from app.data.providers.api_football import LEAGUE_IDS, ApiFootballClient, ApiFootballError, QuotaExceeded
from app.data.team_matching import name_match_score
from app.db.models import Match, MatchLineup, Player, PlayerAbsence, Team

logger = logging.getLogger(__name__)

# Same threshold the fixture importer uses (api_football_ingest._NAME_THRESHOLD)
# -- a lineup's two team blocks would rather go unmatched than get attached to
# the wrong side of the fixture.
_NAME_THRESHOLD = 0.82


def _current_season_start(today: dt.date | None = None) -> int:
    """API-Football names a season by the year it starts: "2025-26" -> 2025.
    Mirrors scripts/bootstrap.py's own copy -- small enough, and in different
    enough contexts (a script vs. an app module), that sharing it isn't
    worth an import pointing the wrong way across that boundary."""

    today = today or dt.date.today()
    return today.year if today.month >= 7 else today.year - 1


def _get_or_create_player(db: Session, api_player_id: int, name: str, team: Team | None) -> Player:
    player = db.execute(select(Player).where(Player.api_player_id == api_player_id)).scalar_one_or_none()
    if player is None:
        player = Player(api_player_id=api_player_id, name=name, team_id=team.id if team else None)
        db.add(player)
        db.flush()
        return player
    # Keep current rather than historical: a transfer or a provider spelling
    # fix should take effect immediately, not leave a stale label.
    player.name = name
    if team is not None:
        player.team_id = team.id
    return player


@dataclass
class InjuryImportReport:
    league: str
    considered: int = 0
    stored: int = 0
    unresolved_teams: list[str] = field(default_factory=list)


def import_injuries(
    db: Session, client: ApiFootballClient, *, league_id: int, league_name: str, season: int
) -> InjuryImportReport:
    """Overwrites this league's stored absences wholesale with the provider's
    current list -- see PlayerAbsence's own docstring for why diffing isn't
    worth it: a recovered player just stops appearing, there's no event to
    react to instead."""

    report = InjuryImportReport(league=league_name)
    rows = client.injuries(league_id=league_id, season=season)
    index = TeamIndex(db)

    db.execute(
        PlayerAbsence.__table__.delete().where(
            PlayerAbsence.team_id.in_(select(Team.id).where(Team.league == league_name))
        )
    )

    for row in rows:
        report.considered += 1
        player_block = row.get("player") or {}
        team_block = row.get("team") or {}
        fixture_block = row.get("fixture") or {}

        api_player_id = player_block.get("id")
        player_name = player_block.get("name")
        if api_player_id is None or not player_name:
            continue

        team_name = team_block.get("name") or ""
        team = index.resolve(team_name, prefer_league=league_name)
        if team is None:
            report.unresolved_teams.append(team_name)
            continue

        match = None
        fixture_id = fixture_block.get("id")
        if fixture_id is not None:
            match = db.execute(select(Match).where(Match.api_fixture_id == fixture_id)).scalar_one_or_none()

        player = _get_or_create_player(db, api_player_id, player_name, team)
        reason = player_block.get("reason") or player_block.get("type")

        db.add(
            PlayerAbsence(
                player_id=player.id,
                team_id=team.id,
                match_id=match.id if match else None,
                reason=reason,
            )
        )
        report.stored += 1

    db.commit()
    return report


def import_injuries_from_settings(
    db: Session, *, league_names: list[str] | None = None, season: int | None = None
) -> list[InjuryImportReport]:
    """The settings-to-client wiring, same shape as
    app.data.api_football_ingest.run_live_sync_from_settings: builds a client
    from stored settings and runs one pass over every tracked league (or the
    given subset). Stops at the first quota/API error rather than silently
    skipping the rest of the leagues -- a half-updated injury list looks
    exactly like a fully-updated one, so a caller needs to know it happened."""

    from app import app_settings  # local import: this module has no other reason to depend on settings

    values = app_settings.all_values(db)
    key = str(values.get("api_football_key") or "")
    if not key:
        return []

    client = ApiFootballClient(
        key,
        host=str(values.get("api_football_host") or "v3.football.api-sports.io"),
        daily_budget=int(values.get("api_football_daily_budget") or 7500),
        per_minute=int(values.get("api_football_per_minute") or 300),
    )
    season = season or _current_season_start()
    reports: list[InjuryImportReport] = []
    for league_name in league_names or list(LEAGUE_IDS):
        league_id = LEAGUE_IDS.get(league_name)
        if league_id is None:
            continue
        try:
            reports.append(import_injuries(db, client, league_id=league_id, league_name=league_name, season=season))
        except (ApiFootballError, QuotaExceeded) as exc:
            logger.warning("Injury import stopped at %s: %s", league_name, exc)
            break
    return reports


@dataclass
class LineupImportReport:
    match_id: int
    stored: int = 0
    already_had_lineup: bool = False


def import_lineup(db: Session, client: ApiFootballClient, match: Match) -> LineupImportReport | None:
    """Fetches and stores one match's confirmed lineup.

    Returns without making a request if this match already has one stored --
    a lineup is set once the teams hand it in and never revised, so
    re-fetching after that only spends quota on an answer that cannot
    change. Returns ``None`` (not a request either) if the match carries no
    API-Football fixture id to ask about.

    An empty response is the normal case outside the ~60-75 minute
    pre-kickoff window the provider actually publishes lineups in -- not an
    error, and nothing is stored, so the caller's own retry-on-the-next-poll
    behavior (app.main._live_sync_loop) is what eventually catches the real
    announcement.
    """

    existing = db.execute(
        select(MatchLineup.id).where(MatchLineup.match_id == match.id).limit(1)
    ).scalar_one_or_none()
    if existing is not None:
        return LineupImportReport(match_id=match.id, already_had_lineup=True)

    if match.api_fixture_id is None:
        return None

    rows = client.lineups(match.api_fixture_id)
    report = LineupImportReport(match_id=match.id)
    if not rows:
        return report

    for block in rows:
        team_name = (block.get("team") or {}).get("name") or ""
        # Settled by comparing against this fixture's own two teams rather
        # than a league-wide TeamIndex lookup: this match already knows
        # exactly which two clubs it is, so there's no ambiguity to resolve
        # the way a fresh fixture import has to.
        home_score, _ = name_match_score(team_name, match.home_team.name)
        away_score, _ = name_match_score(team_name, match.away_team.name)
        if max(home_score, away_score) < _NAME_THRESHOLD:
            continue
        team = match.home_team if home_score >= away_score else match.away_team

        for entry in block.get("startXI") or []:
            report.stored += _store_lineup_player(db, match, team, entry.get("player") or {}, is_starter=True)
        for entry in block.get("substitutes") or []:
            report.stored += _store_lineup_player(db, match, team, entry.get("player") or {}, is_starter=False)

    db.commit()
    return report


@dataclass
class LineupCheckRun:
    considered: int = 0
    # match_ids whose lineup was fetched and stored for the first time on
    # this run -- the signal app.main uses to decide which predictions to
    # regenerate, since a match already checked (already_had_lineup) or
    # still unconfirmed (empty response) needs nothing done for it.
    newly_confirmed: list[int] = field(default_factory=list)


def run_lineup_check_from_settings(db: Session, *, window_minutes: int = 90) -> LineupCheckRun | None:
    """Checks every SCHEDULED match kicking off within ``window_minutes`` for
    a now-confirmed lineup. Same settings-to-client wiring as
    run_live_sync_from_settings/import_injuries_from_settings; returns
    ``None`` (no request made) with no API-Football key configured.

    Stops at the first quota/API error rather than skipping the rest of this
    run's matches silently -- same reasoning as import_injuries_from_settings."""

    from app import app_settings  # local import: this module has no other reason to depend on settings

    values = app_settings.all_values(db)
    key = str(values.get("api_football_key") or "")
    if not key:
        return None

    client = ApiFootballClient(
        key,
        host=str(values.get("api_football_host") or "v3.football.api-sports.io"),
        daily_budget=int(values.get("api_football_daily_budget") or 7500),
        per_minute=int(values.get("api_football_per_minute") or 300),
    )

    now = dt.datetime.utcnow()
    window_end = now + dt.timedelta(minutes=window_minutes)
    matches = db.execute(
        select(Match).where(
            Match.status == "SCHEDULED",
            Match.api_fixture_id.is_not(None),
            Match.date >= now,
            Match.date <= window_end,
        )
    ).scalars().all()

    run = LineupCheckRun()
    for match in matches:
        run.considered += 1
        try:
            report = import_lineup(db, client, match)
        except (ApiFootballError, QuotaExceeded) as exc:
            logger.warning("Lineup check stopped at match %s: %s", match.id, exc)
            break
        if report is not None and report.stored and not report.already_had_lineup:
            run.newly_confirmed.append(match.id)
    return run


def _store_lineup_player(db: Session, match: Match, team: Team, player_block: dict, *, is_starter: bool) -> int:
    api_player_id = player_block.get("id")
    name = player_block.get("name")
    if api_player_id is None or not name:
        return 0
    player = _get_or_create_player(db, api_player_id, name, team)
    db.add(MatchLineup(match_id=match.id, team_id=team.id, player_id=player.id, is_starter=is_starter))
    return 1
