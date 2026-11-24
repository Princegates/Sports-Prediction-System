#!/usr/bin/env python3
"""Explain, step by step, how one upcoming match's prediction was reached.

Read-only: it never writes. Use it when a prediction looks wrong, to see which
part is off -- a club with no rating history, two clubs from different leagues
compared as if equal, or one model disagreeing with the other.

    python scripts/diagnose_match.py Sabah Barcelona

Prints the match and competition, each club's own league, how many results sit
behind its rating, the Elo and Poisson probabilities separately (and what the
blend does with them), and how many matches the data-quality score counted.
Nothing in the output is secret: team names, dates and numbers only.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, or_, select

from app.config import get_settings
from app.db.models import EloHistory, Match, Team
from app.db.session import SessionLocal
from app.features.team_stats import matches_played_before
from app.prediction_models import elo, league_strength, poisson_model
from app.prediction_models.ensemble import EnsembleWeights


def pct(value: float) -> str:
    return f"{value * 100:5.1f}%"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("home", help="part of the home team's name")
    parser.add_argument("away", help="part of the away team's name")
    args = parser.parse_args()
    settings = get_settings()

    with SessionLocal() as db:
        home_t, away_t = Team.__table__.alias("h"), Team.__table__.alias("a")
        rows = db.execute(
            select(Match)
            .join(home_t, Match.home_team_id == home_t.c.id)
            .join(away_t, Match.away_team_id == away_t.c.id)
            .where(home_t.c.name.ilike(f"%{args.home}%"), away_t.c.name.ilike(f"%{args.away}%"))
            .order_by(Match.date.desc())
            .limit(5)
        ).scalars().all()
        if not rows:
            print("No match found with those names (home first, then away). Try shorter fragments.")
            return 1

        match = rows[0]
        if len(rows) > 1:
            print(f"{len(rows)} matches found; using the latest. Others: " + ", ".join(f"#{m.id} {m.date:%Y-%m-%d} {m.league}" for m in rows[1:]))
        home, away = db.get(Team, match.home_team_id), db.get(Team, match.away_team_id)

        print(f"\nMatch #{match.id}: {home.name} vs {away.name}")
        print(f"  Competition: {match.league}   Date: {match.date}   Season: {match.season}")
        print(f"  Same competition as a trained model? European competition: {match.league in elo.EUROPEAN_COMPETITIONS}")

        print("\nEach club's own record in the database")
        for team in (home, away):
            domestic_rows = db.execute(
                select(func.count()).select_from(EloHistory).where(EloHistory.team_id == team.id)
            ).scalar() or 0
            all_played = db.execute(
                select(func.count()).select_from(Match).where(
                    or_(Match.home_team_id == team.id, Match.away_team_id == team.id),
                    Match.home_score.is_not(None), Match.date < match.date,
                )
            ).scalar() or 0
            rating = elo.get_rating_before(db, team.id, match.date, settings.elo_start_rating)
            known = db.execute(
                select(EloHistory.id).where(EloHistory.team_id == team.id, EloHistory.date < match.date).limit(1)
            ).first() is not None
            print(
                f"  {team.name}: team.league={team.league!r}, country={team.country!r}, "
                f"Elo={rating:.0f} ({'from results' if known else 'NO HISTORY: default start rating'}), "
                f"Elo updates stored={domestic_rows}, finished matches in all competitions={all_played}"
            )

        home_elo = elo.get_rating_before(db, home.id, match.date, settings.elo_start_rating)
        away_elo = elo.get_rating_before(db, away.id, match.date, settings.elo_start_rating)
        advantage = settings.home_advantage_elo
        print("\nElo model")
        if match.league in elo.EUROPEAN_COMPETITIONS:
            strength = league_strength.LeagueStrength.load(db)
            cal = league_strength.calibrate(
                db, strength, competition=match.league, home_team_id=home.id, away_team_id=away.id,
                home_elo=home_elo, away_elo=away_elo, default_home_advantage=advantage,
            )
            home_elo, away_elo, advantage = cal.home, cal.away, cal.home_advantage
            print(f"  League offsets known: {sorted(strength.offsets)}")
            print(f"  Calibration applied: {cal.applied}  {cal.note or '(none: an unknown league gets no offset)'}")
        diff = (home_elo + advantage) - away_elo
        e = elo.elo_diff_to_1x2(diff)
        print(f"  Effective ratings: home {home_elo:.0f} (+{advantage:.0f} home advantage) vs away {away_elo:.0f}  ->  diff {diff:+.0f}")
        print(f"  Elo says:     home {pct(e.home_win)}  draw {pct(e.draw)}  away {pct(e.away_win)}")

        print("\nPoisson goal model")
        ratings = poisson_model.fit_team_ratings(db, match.league, match.date)
        if ratings is None:
            print("  No fitted ratings for this competition: the fixed default goal rates 1.45 v 1.15 are used.")
        lam_h, lam_a = poisson_model.expected_goals(db, home.id, away.id, match.league, match.date)
        g = poisson_model.predict(db, home.id, away.id, match.league, match.date)
        print(f"  Expected goals: home {lam_h:.2f}, away {lam_a:.2f}")
        print(f"  Poisson says: home {pct(g.home_win)}  draw {pct(g.draw)}  away {pct(g.away_win)}")

        weights = EnsembleWeights.from_settings(db)
        print(f"\nBlend weights: {weights}")

        print("\nData-quality score")
        in_comp_h = matches_played_before(db, home.id, match.date, match.league)
        in_comp_a = matches_played_before(db, away.id, match.date, match.league)
        print(
            f"  Counts only matches in {match.league!r}: {home.name}={in_comp_h}, {away.name}={in_comp_a} "
            f"(needs 10 each for 100%). It does not count the club's domestic results."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
