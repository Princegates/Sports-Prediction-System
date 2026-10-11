"""app.main's in-process daily-random-picks loop -- the "Random Daily
Picks" the known-late GitHub Actions schedule used to be the only source
for (see app.random_picks's own docstring for why this moved in-process).

Only _run_random_daily_picks_once is tested directly: it's the one piece
of genuinely new logic (generate_cadence itself is already covered end to
end by test_generate_random_picks.py, via the CLI wrapper) -- specifically
that it actually commits, since generate_cadence leaves that to its caller.
The surrounding async loop (_random_picks_loop) is a bare
try/sleep/repeat wrapper, same shape as the already-untested
_live_sync_loop/_lineup_check_loop above it, so it isn't tested on its
own either.
"""

from __future__ import annotations

import datetime as dt

from app.db.models import AdminPick, Match, Prediction, Team
from app.main import _run_random_daily_picks_once

BASE = dt.datetime.utcnow() + dt.timedelta(hours=6)


def _prediction(match_id: int) -> Prediction:
    return Prediction(
        match_id=match_id, model_version="test", home_win=0.75, draw=0.15, away_win=0.10,
        over_probabilities={"2.5": 0.60}, btts_yes=0.55, btts_no=0.45,
        correct_score_probabilities={"2-1": 0.11}, most_likely_score="2-1", most_likely_score_probability=0.11,
        global_outcome_market="Match Result", global_outcome_selection="Home Win", global_outcome_probability=0.75,
        confidence="HIGH", data_quality_score=1.0, model_agreement_score=0.9,
        explanation={"positive": [], "negative": []}, model_breakdown={},
    )


def _favorite_matches(db_session, count: int = 20) -> None:
    for i in range(count):
        home = Team(name=f"Home{i}", league="English Premier League", aliases=[])
        away = Team(name=f"Away{i}", league="English Premier League", aliases=[])
        db_session.add_all([home, away])
        db_session.commit()
        db_session.refresh(home)
        db_session.refresh(away)
        match = Match(
            league="English Premier League", season="2025-26", date=BASE + dt.timedelta(days=1, hours=i),
            home_team_id=home.id, away_team_id=away.id, status="SCHEDULED",
        )
        db_session.add(match)
        db_session.commit()
        db_session.refresh(match)
        db_session.add(_prediction(match.id))
    db_session.commit()


def test_one_run_writes_and_commits_one_daily_slip(db_session):
    _favorite_matches(db_session)

    _run_random_daily_picks_once()

    db_session.expire_all()
    sources = {p.source for p in db_session.query(AdminPick).all()}
    assert sources == {"system_random_daily_1"}


def test_it_never_touches_the_weekly_cadence(db_session):
    _favorite_matches(db_session)
    weekly = AdminPick(
        source="system_random_weekly_1", legs=[], priced=False, label="Last week's draw #1",
        expires_at=dt.datetime.utcnow() + dt.timedelta(days=5),
    )
    db_session.add(weekly)
    db_session.commit()
    db_session.refresh(weekly)

    _run_random_daily_picks_once()

    db_session.expire_all()
    untouched = db_session.get(AdminPick, weekly.id)
    assert untouched is not None
    assert untouched.label == "Last week's draw #1"
