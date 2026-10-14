#!/usr/bin/env python3
"""Read-only: full detail on the two Champions League rows
audit_european_fixtures.py flags as "Real Madrid CF plays itself" on
2025-03-04 and 2025-03-12 -- home_team_id and away_team_id, scores, status,
provider fixture id, and whatever Prediction/MatchOdds/EloHistory/
LivePrediction rows reference them, so the actual fix (which side is
mis-resolved, and to which real club) is read from the data rather than
assumed.

    python scripts/inspect_real_madrid_duplicates.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import datetime as dt

from sqlalchemy import func, select

from app.db.models import EloHistory, LivePrediction, Match, MatchOdds, Prediction, Team
from app.db.session import SessionLocal

TARGET_DATES = [dt.date(2025, 3, 4), dt.date(2025, 3, 12)]


def main() -> None:
    db = SessionLocal()
    try:
        rm = db.execute(select(Team).where(Team.name == "Real Madrid CF")).scalar_one()
        print(f"Real Madrid CF: Team.id={rm.id}, league={rm.league!r}\n")

        matches = list(
            db.execute(
                select(Match).where(
                    Match.league == "UEFA Champions League",
                    func.date(Match.date).in_([d.isoformat() for d in TARGET_DATES]),
                    (Match.home_team_id == rm.id) | (Match.away_team_id == rm.id),
                )
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

            pred = db.execute(select(func.count()).select_from(Prediction).where(Prediction.match_id == m.id)).scalar()
            odds = db.execute(select(func.count()).select_from(MatchOdds).where(MatchOdds.match_id == m.id)).scalar()
            elo = db.execute(select(func.count()).select_from(EloHistory).where(EloHistory.match_id == m.id)).scalar()
            live = db.execute(select(func.count()).select_from(LivePrediction).where(LivePrediction.match_id == m.id)).scalar()
            print(f"  references      : {pred} prediction(s), {odds} odds row(s), {elo} elo row(s), {live} live event(s)")
            print()

        atm = db.execute(select(Team).where(Team.name.ilike("%Atletico%Madrid%"))).scalars().all()
        print("Clubs matching '%Atletico%Madrid%':")
        for t in atm:
            print(f"  Team.id={t.id} name={t.name!r} league={t.league!r}")

        print("\nAtletico Madrid's other Champions League fixtures around these dates:")
        if atm:
            for t in atm:
                rows = list(
                    db.execute(
                        select(Match).where(
                            Match.league == "UEFA Champions League",
                            (Match.home_team_id == t.id) | (Match.away_team_id == t.id),
                            Match.date >= dt.datetime(2025, 2, 1),
                            Match.date <= dt.datetime(2025, 4, 1),
                        ).order_by(Match.date)
                    ).scalars()
                )
                for m in rows:
                    home = db.get(Team, m.home_team_id)
                    away = db.get(Team, m.away_team_id)
                    print(f"  #{m.id} {m.date}  {home.name} vs {away.name}  {m.home_score}-{m.away_score}  [{m.status}]")
    finally:
        db.close()


if __name__ == "__main__":
    main()
