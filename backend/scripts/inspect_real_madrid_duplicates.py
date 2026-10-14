#!/usr/bin/env python3
"""Read-only: full detail on the two Champions League rows
audit_european_fixtures.py flags as "Real Madrid CF plays itself" on
2025-03-04 and 2025-03-12 -- team ids, score, status, source, provider
fixture id, and every row in every table with a foreign key to matches.id
that references them, so a fix is read from the data rather than assumed.

Also lists every Spanish La Liga club with "madrid" in its name (accent-
insensitive -- "Atletico" vs "Atlético" defeats a plain ILIKE) and every
Real Madrid fixture, any league, in the surrounding window, in case the
real Real Madrid vs Atletico Madrid tie this pair of rows displaced is
already stored correctly elsewhere.

    python scripts/inspect_real_madrid_duplicates.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import datetime as dt

from sqlalchemy import func, or_, select

from app.db.models import (
    ChatMessage,
    EloHistory,
    FeaturedPick,
    LivePrediction,
    Match,
    MatchLineup,
    MatchOdds,
    MatchView,
    PlayerAbsence,
    Prediction,
    Team,
)
from app.db.session import SessionLocal

TARGET_DATES = [dt.date(2025, 3, 4), dt.date(2025, 3, 12)]

# (label, model, match_id column) -- every table with a foreign key to
# matches.id (see app/db/models.py), so nothing referencing these rows is
# missed.
REFERENCING_TABLES = [
    ("Prediction", Prediction, Prediction.match_id),
    ("MatchOdds", MatchOdds, MatchOdds.match_id),
    ("EloHistory", EloHistory, EloHistory.match_id),
    ("LivePrediction", LivePrediction, LivePrediction.match_id),
    ("FeaturedPick", FeaturedPick, FeaturedPick.match_id),
    ("MatchLineup", MatchLineup, MatchLineup.match_id),
    ("PlayerAbsence", PlayerAbsence, PlayerAbsence.match_id),
    ("MatchView", MatchView, MatchView.match_id),
    ("ChatMessage (context)", ChatMessage, ChatMessage.context_match_id),
]


def main() -> None:
    db = SessionLocal()
    try:
        rm = db.execute(select(Team).where(Team.name == "Real Madrid CF")).scalar_one()
        print(f"Real Madrid CF: Team.id={rm.id}, league={rm.league!r}\n")

        matches = list(
            db.execute(
                select(Match).where(
                    Match.league == "UEFA Champions League",
                    func.date(Match.date).in_(TARGET_DATES),
                    (Match.home_team_id == rm.id) | (Match.away_team_id == rm.id),
                ).order_by(Match.date)
            ).scalars()
        )

        for m in matches:
            home = db.get(Team, m.home_team_id)
            away = db.get(Team, m.away_team_id)
            print(f"Match #{m.id}")
            print(f"  date            : {m.date}")
            print(f"  home_team_id    : {m.home_team_id} ({home.name!r}, league={home.league!r})")
            print(f"  away_team_id    : {m.away_team_id} ({away.name!r}, league={away.league!r})")
            print(f"  score           : {m.home_score}-{m.away_score}")
            print(f"  status          : {m.status}")
            print(f"  source          : {m.source!r}")
            print(f"  api_fixture_id  : {m.api_fixture_id}")
            print(f"  season          : {m.season!r}")

            refs = []
            for label, _model, col in REFERENCING_TABLES:
                count = db.execute(select(func.count()).select_from(_model).where(col == m.id)).scalar()
                if count:
                    refs.append(f"{count} {label}")
            print(f"  references      : {', '.join(refs) if refs else 'none in any table'}")
            print()

        print("Spanish La Liga clubs with 'madrid' in the name (accent-insensitive):")
        la_liga_madrid = db.execute(
            select(Team).where(Team.league == "Spanish La Liga", func.lower(Team.name).like("%madrid%"))
        ).scalars().all()
        for t in la_liga_madrid:
            print(f"  Team.id={t.id} name={t.name!r} aliases={t.aliases!r}")

        print("\nEvery Real Madrid fixture (any league) from 2025-02-20 to 2025-03-20:")
        window = list(
            db.execute(
                select(Match).where(
                    or_(Match.home_team_id == rm.id, Match.away_team_id == rm.id),
                    Match.date >= dt.datetime(2025, 2, 20),
                    Match.date <= dt.datetime(2025, 3, 20),
                ).order_by(Match.date)
            ).scalars()
        )
        for m in window:
            home = db.get(Team, m.home_team_id)
            away = db.get(Team, m.away_team_id)
            print(f"  #{m.id} {m.date}  [{m.league}]  {home.name} vs {away.name}  {m.home_score}-{m.away_score}  ({m.status}, source={m.source!r})")
    finally:
        db.close()


if __name__ == "__main__":
    main()
