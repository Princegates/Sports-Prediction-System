from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data.providers import football_data_co_uk as fdcu
from app.data.providers import openfootball as ofb
from app.data.providers import thesportsdb as sdb
from app.data.team_matching import normalize_team_name
from app.db.models import Match, Team


def get_or_create_team(db: Session, name: str, league: str) -> Team:
    team = db.execute(select(Team).where(Team.name == name, Team.league == league)).scalar_one_or_none()
    if team is not None:
        return team

    # No exact match -- check whether this is just a naming variant of a
    # team we already have for this league (see team_matching.py) before
    # creating a duplicate that would fragment its history.
    target_key = normalize_team_name(name)
    for candidate in db.execute(select(Team).where(Team.league == league)).scalars():
        if normalize_team_name(candidate.name) == target_key or name in candidate.aliases:
            if name not in candidate.aliases:
                candidate.aliases = [*candidate.aliases, name]
                db.flush()
            return candidate

    team = Team(name=name, league=league, aliases=[])
    db.add(team)
    db.flush()
    return team


def import_historical_season(db: Session, league_code: str, season: str) -> int:
    """Import one season of results from football-data.co.uk. Returns the
    number of matches inserted (existing matches for the same fixture are
    skipped, so this is safe to re-run)."""

    league_name = fdcu.LEAGUE_CODES.get(league_code, league_code)
    raw_matches = fdcu.fetch_season(league_code, season)

    inserted = 0
    for rm in raw_matches:
        home = get_or_create_team(db, rm.home_team, league_name)
        away = get_or_create_team(db, rm.away_team, league_name)

        existing = db.execute(
            select(Match).where(
                Match.league == league_name,
                Match.date == rm.date,
                Match.home_team_id == home.id,
                Match.away_team_id == away.id,
            )
        ).scalar_one_or_none()
        if existing is not None:
            continue

        db.add(
            Match(
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
        )
        inserted += 1

    db.commit()
    return inserted


def import_upcoming_fixtures(db: Session, league_name: str, api_key: str = "3") -> int:
    fixtures = sdb.fetch_upcoming_fixtures(league_name, api_key=api_key)

    inserted = 0
    for fixture in fixtures:
        home = get_or_create_team(db, fixture.home_team, league_name)
        away = get_or_create_team(db, fixture.away_team, league_name)

        existing = db.execute(
            select(Match).where(
                Match.league == league_name,
                Match.home_team_id == home.id,
                Match.away_team_id == away.id,
                Match.date == fixture.date,
            )
        ).scalar_one_or_none()
        if existing is not None:
            continue

        season = _season_label(fixture.date)
        db.add(
            Match(
                league=league_name,
                season=season,
                date=fixture.date,
                home_team_id=home.id,
                away_team_id=away.id,
                home_score=None,
                away_score=None,
                status="SCHEDULED",
                source="thesportsdb",
            )
        )
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

    inserted = updated = 0
    for rm in raw_matches:
        home = get_or_create_team(db, rm.home_team, league_name)
        away = get_or_create_team(db, rm.away_team, league_name)

        existing = db.execute(
            select(Match).where(
                Match.league == league_name,
                Match.season == season,
                Match.home_team_id == home.id,
                Match.away_team_id == away.id,
            )
        ).scalar_one_or_none()

        if existing is None:
            db.add(
                Match(
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
            )
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
