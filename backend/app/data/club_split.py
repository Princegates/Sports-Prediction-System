"""Separating two real clubs that were wrongly merged into one row.

Once two clubs share a row, nothing in the database remembers which match
was whose -- every one points at the survivor. API-Football does remember:
each of its fixtures names both clubs. So the club that was merged away is
recreated, and for every provider fixture naming it, the stored match for
that fixture (same competition, within a day, the other club the same, the
same score) has its side handed back. A match no provider fixture claims for
the restored club stays with the survivor, which is where the survivor's own
matches belong. Nothing is guessed from names or dates alone.

Written after a cleanup run merged Inter into AC Milan; see
scripts/split_merged_club.py for running it.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.data.api_football_ingest import TeamIndex, _parse_kickoff
from app.data.providers.api_football import LEAGUE_IDS, ApiFootballClient
from app.db.models import EloHistory, Match, Team
from app.prediction_models.elo import EUROPEAN_COMPETITIONS

SAME_MATCH_WINDOW = dt.timedelta(days=1)


@dataclass
class SplitReport:
    restored_team_id: int | None = None
    created: bool = False
    reassigned: int = 0
    already_right: int = 0
    unmatched: list[str] = field(default_factory=list)
    ambiguous: list[str] = field(default_factory=list)
    requests_used: int = 0
    per_competition: dict[str, int] = field(default_factory=dict)


def split_club(
    db: Session,
    client: ApiFootballClient,
    *,
    league: str,
    merged_name: str,
    restore_name: str,
    restore_aliases: list[str],
    competitions: list[str],
    seasons: list[int],
    apply: bool = False,
) -> SplitReport:
    report = SplitReport()

    merged = db.execute(select(Team).where(Team.league == league, Team.name == merged_name)).scalar_one_or_none()
    if merged is None:
        raise ValueError(f"No club named {merged_name!r} in {league}")

    restored = db.execute(select(Team).where(Team.league == league, Team.name == restore_name)).scalar_one_or_none()
    spellings = {restore_name, *restore_aliases}
    if restored is None:
        restored = Team(name=restore_name, league=league, aliases=[])
        db.add(restored)
        report.created = True
    restored.aliases = sorted((set(restored.aliases or []) | spellings) - {restore_name})
    # Left on the survivor, these would send every provider mention of the
    # restored club straight back to it.
    merged.aliases = sorted(set(merged.aliases or []) - spellings)
    db.flush()
    report.restored_team_id = restored.id

    index = TeamIndex(db)

    for competition in competitions:
        prefer = None if competition in EUROPEAN_COMPETITIONS else competition
        for season in seasons:
            rows = client.fixtures(league_id=LEAGUE_IDS[competition], season=season)
            for row in rows:
                teams = row.get("teams") or {}
                fixture = row.get("fixture") or {}
                goals = row.get("goals") or {}
                home_name = (teams.get("home") or {}).get("name") or ""
                away_name = (teams.get("away") or {}).get("name") or ""
                home = index.resolve(home_name, prefer_league=prefer)
                away = index.resolve(away_name, prefer_league=prefer)
                if restored not in (home, away):
                    continue
                if home is None or away is None:
                    report.unmatched.append(f"{home_name} vs {away_name} ({competition} {season}): other club not held")
                    continue

                # What the stored row for this fixture looks like now: the
                # restored club's side still points at the survivor.
                def stored_ids(team: Team) -> set[int]:
                    return {merged.id, restored.id} if team.id == restored.id else {team.id}

                kickoff = _parse_kickoff(fixture.get("date") or "")
                finished = ((fixture.get("status") or {}).get("short") or "").upper() in {"FT", "AET", "PEN"}
                candidates = [
                    m for m in db.execute(
                        select(Match).where(
                            Match.league == competition,
                            Match.date >= kickoff - SAME_MATCH_WINDOW,
                            Match.date <= kickoff + SAME_MATCH_WINDOW,
                        )
                    ).scalars()
                    if m.home_team_id in stored_ids(home)
                    and m.away_team_id in stored_ids(away)
                    and not (
                        finished and m.home_score is not None
                        and (m.home_score, m.away_score) != (goals.get("home"), goals.get("away"))
                    )
                ]
                label = f"{home_name} vs {away_name} ({kickoff:%Y-%m-%d}, {competition})"
                if not candidates:
                    report.unmatched.append(f"{label}: not stored")
                    continue
                if len(candidates) > 1:
                    report.ambiguous.append(f"{label}: {len(candidates)} stored rows fit")
                    continue

                match = candidates[0]
                changed = False
                if home.id == restored.id and match.home_team_id == merged.id:
                    match.home_team_id = restored.id
                    changed = True
                if away.id == restored.id and match.away_team_id == merged.id:
                    match.away_team_id = restored.id
                    changed = True
                if not changed:
                    report.already_right += 1
                    continue
                # Its Elo snapshot follows, except in a derby against the
                # survivor, where both snapshots point at the survivor and
                # can't be told apart -- a retrain rebuilds Elo from scratch.
                if merged.id not in (match.home_team_id, match.away_team_id):
                    db.execute(
                        update(EloHistory)
                        .where(EloHistory.match_id == match.id, EloHistory.team_id == merged.id)
                        .values(team_id=restored.id)
                    )
                report.reassigned += 1
                report.per_competition[competition] = report.per_competition.get(competition, 0) + 1

    report.requests_used = client.quota.used_this_run
    if apply:
        db.commit()
    else:
        db.rollback()
    return report
