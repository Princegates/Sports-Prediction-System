"""Separating Inter back out of AC Milan -- see app/data/club_split.py.

Mirrors what a cleanup run did in production: Inter's row was merged into
AC Milan's, so every Inter match -- league, derby, European -- pointed at
"AC Milan", with "FC Internazionale Milano" left in its aliases.
"""

from __future__ import annotations

import datetime as dt

from app.data.api_football_ingest import TeamIndex
from app.data.club_split import split_club
from app.db.models import EloHistory, Match, Team
from tests.test_api_football import FakeClient

IT, UEL = "Italian Serie A", "UEFA Europa League"


def _fixture(fid, date, home, away, score):
    return {
        "fixture": {"id": fid, "date": f"{date}+00:00", "status": {"short": "FT"}},
        "teams": {"home": {"name": home}, "away": {"name": away}},
        "goals": {"home": score[0], "away": score[1]},
    }


class PerCompetitionClient(FakeClient):
    """Returns each competition's own fixture list."""

    def __init__(self, by_league: dict[int, list[dict]]):
        super().__init__({})
        self.by_league = by_league

    def get(self, path, params=None):
        super().get(path, params)
        return self.by_league.get((params or {}).get("league"), [])


def test_inter_is_separated_back_out_of_ac_milan(db_session):
    db = db_session
    merged = Team(name="AC Milan", league=IT, aliases=["FC Internazionale Milano"])
    napoli, torino = Team(name="SSC Napoli", league=IT, aliases=[]), Team(name="Torino FC", league=IT, aliases=[])
    porto = Team(name="FC Porto", league="Portuguese Primeira Liga", aliases=[])
    db.add_all([merged, napoli, torino, porto])
    db.flush()

    def match(league, date, home, away, score):
        m = Match(league=league, season="x", date=date, home_team_id=home.id, away_team_id=away.id,
                  status="FINISHED", home_score=score[0], away_score=score[1])
        db.add(m)
        db.flush()
        return m

    inter_home = match(IT, dt.datetime(2024, 10, 5, 20, 45), merged, napoli, (2, 0))   # really Inter
    milan_home = match(IT, dt.datetime(2024, 10, 6, 20, 45), merged, torino, (1, 1))   # really Milan
    derby = match(IT, dt.datetime(2024, 11, 3, 20, 45), merged, merged, (1, 2))       # Inter vs Milan
    inter_euro = match(UEL, dt.datetime(2026, 9, 24, 21, 0), porto, merged, (0, 3))    # really Inter
    db.add(EloHistory(team_id=merged.id, match_id=inter_home.id, date=inter_home.date,
                      rating_before=1600, rating_after=1610))
    db.commit()

    client = PerCompetitionClient({
        135: [
            _fixture(1, "2024-10-05T18:45:00", "Inter", "Napoli", (2, 0)),
            _fixture(2, "2024-10-06T18:45:00", "AC Milan", "Torino", (1, 1)),
            _fixture(3, "2024-11-03T19:45:00", "Inter", "AC Milan", (1, 2)),
        ],
        3: [_fixture(4, "2026-09-24T19:00:00", "FC Porto", "Inter", (0, 3))],
    })

    dry = split_club(db, client, league=IT, merged_name="AC Milan", restore_name="Inter",
                     restore_aliases=["FC Internazionale Milano"], competitions=[IT, UEL], seasons=[2024])
    assert dry.reassigned == 3
    db.expire_all()
    assert db.query(Team).filter_by(league=IT, name="Inter").count() == 0, "a dry run writes nothing"

    report = split_club(db, client, league=IT, merged_name="AC Milan", restore_name="Inter",
                        restore_aliases=["FC Internazionale Milano"], competitions=[IT, UEL], seasons=[2024],
                        apply=True)

    assert report.created and report.reassigned == 3
    inter = db.query(Team).filter_by(league=IT, name="Inter").one()
    milan = db.query(Team).filter_by(league=IT, name="AC Milan").one()
    assert "FC Internazionale Milano" not in milan.aliases
    assert "FC Internazionale Milano" in inter.aliases

    assert db.get(Match, inter_home.id).home_team_id == inter.id
    assert db.get(Match, milan_home.id).home_team_id == milan.id, "Milan's own match stays"
    d = db.get(Match, derby.id)
    assert (d.home_team_id, d.away_team_id) == (inter.id, milan.id)
    assert db.get(Match, inter_euro.id).away_team_id == inter.id
    assert db.query(EloHistory).filter_by(match_id=inter_home.id).one().team_id == inter.id

    index = TeamIndex(db)
    assert index.resolve("Inter", prefer_league=IT).id == inter.id
    assert index.resolve("AC Milan", prefer_league=IT).id == milan.id

    again = split_club(db, client, league=IT, merged_name="AC Milan", restore_name="Inter",
                       restore_aliases=["FC Internazionale Milano"], competitions=[IT, UEL], seasons=[2024],
                       apply=True)
    assert again.reassigned == 0 and again.already_right == 3, "safe to re-run"
