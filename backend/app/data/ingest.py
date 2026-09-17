from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data.providers import football_data_co_uk as fdcu
from app.data.providers import thesportsdb as sdb
from app.db.models import Match, Team


def get_or_create_team(db: Session, name: str, league: str) -> Team:
    team = db.execute(select(Team).where(Team.name == name, Team.league == league)).scalar_one_or_none()
    if team is None:
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


def _season_label(date: dt.datetime) -> str:
    # European season convention: Aug-Jul, labeled by the starting year pair.
    start_year = date.year if date.month >= 7 else date.year - 1
    return f"{start_year % 100:02d}{(start_year + 1) % 100:02d}"
