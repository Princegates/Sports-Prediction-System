from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data.providers import football_data_co_uk as fdcu
from app.data.providers import openfootball as ofb
from app.data.providers import thesportsdb as sdb
from app.data.team_matching import normalize_team_name
from app.db.models import Match, Team


class TeamResolver:
    """Resolves team names to ``Team`` rows, reading the league's teams once.

    The obvious implementation queries per lookup, which is what this replaced.
    That is fine against local SQLite and badly wrong against a managed
    Postgres: name resolution ran up to two queries per team per match, one of
    them a full ``SELECT`` of every team in the league, so a five-league import
    issued tens of thousands of round trips. On a local database that is
    microseconds each and invisible; from a laptop to a database on another
    continent it is ~200ms each, turning a ten-minute import into an overnight
    one.

    Holding the league's teams in two dicts makes resolution O(1) with no
    further I/O. A league has a few dozen teams, so the memory cost is nil.
    """

    def __init__(self, db: Session, league: str) -> None:
        self.db = db
        self.league = league
        # Exact spellings (including known aliases) -> Team
        self._by_name: dict[str, Team] = {}
        # Suffix-stripped key -> Team, for matching naming variants across
        # sources ("Real Madrid" vs "Real Madrid CF").
        self._by_key: dict[str, Team] = {}

        for team in db.execute(select(Team).where(Team.league == league)).scalars():
            self._index(team)

    def _index(self, team: Team) -> None:
        self._by_name[team.name] = team
        self._by_key.setdefault(normalize_team_name(team.name), team)
        for alias in team.aliases or []:
            self._by_name[alias] = team

    def get_or_create(self, name: str) -> Team:
        existing = self._by_name.get(name)
        if existing is not None:
            return existing

        # Not a spelling we've seen. Is it a variant of a team we already
        # have? Matching on the normalized key prevents a second Team row
        # that would fragment the club's Elo rating and match history.
        candidate = self._by_key.get(normalize_team_name(name))
        if candidate is not None:
            candidate.aliases = [*(candidate.aliases or []), name]
            self._by_name[name] = candidate
            return candidate

        team = Team(name=name, league=self.league, aliases=[])
        self.db.add(team)
        # Flushed because the caller needs team.id to build the Match row.
        self.db.flush()
        self._index(team)
        return team


def get_or_create_team(db: Session, name: str, league: str) -> Team:
    """Single-shot team resolution.

    Kept for callers outside an import loop. Inside one, build a
    ``TeamResolver`` once and reuse it -- this rebuilds the league index on
    every call.
    """

    return TeamResolver(db, league).get_or_create(name)


def _existing_by_pairing(db: Session, league: str, season: str | None = None) -> dict[tuple, Match]:
    """Every already-imported match for a league (optionally one season),
    keyed by the pairing the import loop looks up.

    One query up front instead of one per match -- the same round-trip
    problem as team resolution, and the larger half of it.
    """

    stmt = select(Match).where(Match.league == league)
    if season is not None:
        stmt = stmt.where(Match.season == season)
    return {
        (m.home_team_id, m.away_team_id, m.date): m
        for m in db.execute(stmt).scalars()
    }


def import_historical_season(db: Session, league_code: str, season: str) -> int:
    """Import one season of results from football-data.co.uk. Returns the
    number of matches inserted (existing matches for the same fixture are
    skipped, so this is safe to re-run)."""

    league_name = fdcu.LEAGUE_CODES.get(league_code, league_code)
    raw_matches = fdcu.fetch_season(league_code, season)

    teams = TeamResolver(db, league_name)
    existing_matches = _existing_by_pairing(db, league_name)

    inserted = 0
    for rm in raw_matches:
        home = teams.get_or_create(rm.home_team)
        away = teams.get_or_create(rm.away_team)

        if (home.id, away.id, rm.date) in existing_matches:
            continue

        match = Match(
            league=league_name,
            season=rm.season,
            date=rm.date,
            home_team_id=home.id,
            away_team_id=away.id,
            home_score=rm.home_score,
            away_score=rm.away_score,
            ht_home_score=rm.ht_home_score,
            ht_away_score=rm.ht_away_score,
            status="FINISHED",
            source="football-data.co.uk",
        )
        db.add(match)
        existing_matches[(home.id, away.id, rm.date)] = match
        inserted += 1

    db.commit()
    return inserted


def import_upcoming_fixtures(db: Session, league_name: str, api_key: str = "3") -> int:
    fixtures = sdb.fetch_upcoming_fixtures(league_name, api_key=api_key)

    teams = TeamResolver(db, league_name)
    existing_matches = _existing_by_pairing(db, league_name)

    inserted = 0
    for fixture in fixtures:
        home = teams.get_or_create(fixture.home_team)
        away = teams.get_or_create(fixture.away_team)

        if (home.id, away.id, fixture.date) in existing_matches:
            continue

        match = Match(
            league=league_name,
            season=_season_label(fixture.date),
            date=fixture.date,
            home_team_id=home.id,
            away_team_id=away.id,
            home_score=None,
            away_score=None,
            status="SCHEDULED",
            source="thesportsdb",
        )
        db.add(match)
        existing_matches[(home.id, away.id, fixture.date)] = match
        inserted += 1

    db.commit()
    return inserted


def import_openfootball_season(db: Session, league_name: str, season: str) -> dict[str, int]:
    """Import one season from openfootball/football.json. Each match is
    either FINISHED (score present) or SCHEDULED (score absent -- a genuine
    not-yet-played fixture, not a placeholder). Re-running this is what
    turns a previously-SCHEDULED match into FINISHED once it's actually
    been played and the source file has been updated upstream."""

    raw_matches = ofb.fetch_season(league_name, season)

    teams = TeamResolver(db, league_name)
    # Matched on (home, away) within the season rather than including the
    # date, because a fixture's date can move after it was first imported --
    # keying on date would insert a duplicate instead of updating it.
    existing_matches = {
        (m.home_team_id, m.away_team_id): m
        for m in db.execute(
            select(Match).where(Match.league == league_name, Match.season == season)
        ).scalars()
    }

    inserted = updated = 0
    for rm in raw_matches:
        home = teams.get_or_create(rm.home_team)
        away = teams.get_or_create(rm.away_team)

        existing = existing_matches.get((home.id, away.id))

        if existing is None:
            match = Match(
                league=league_name,
                season=season,
                date=rm.date,
                home_team_id=home.id,
                away_team_id=away.id,
                home_score=rm.home_score,
                away_score=rm.away_score,
                ht_home_score=rm.ht_home_score,
                ht_away_score=rm.ht_away_score,
                status="FINISHED" if rm.is_played else "SCHEDULED",
                source="openfootball/football.json",
            )
            db.add(match)
            existing_matches[(home.id, away.id)] = match
            inserted += 1
        elif rm.is_played and existing.home_score is None:
            existing.home_score = rm.home_score
            existing.away_score = rm.away_score
            existing.ht_home_score = rm.ht_home_score
            existing.ht_away_score = rm.ht_away_score
            existing.status = "FINISHED"
            existing.date = rm.date
            updated += 1

    db.commit()
    return {"inserted": inserted, "updated": updated}


def _season_label(date: dt.datetime) -> str:
    # European season convention: Aug-Jul, labeled by the starting year pair.
    start_year = date.year if date.month >= 7 else date.year - 1
    return f"{start_year % 100:02d}{(start_year + 1) % 100:02d}"
