#!/usr/bin/env python3
"""Read-only: investigates the "Sabah FA vs Borussia Dortmund" UEFA Champions
League match a member flagged as implausible (68% home win for Sabah FA,
odds on SportyBet showing Dortmund as a heavy favourite instead).

Prints, for the match and both teams:
  - the Team rows (id, league, country, aliases) -- whether "Sabah FA" is a
    genuine pre-existing club row or something odd
  - every Match row either team appears in, to spot a row that looks
    misattached (e.g. Sabah FA "playing" far outside any league it has a
    real history in)
  - this fixture's own row: api_fixture_id, status, date, scores -- an
    api_fixture_id is API-Football's own id, the only way to tell whether
    upstream really sent this exact pairing or whether a later import
    folded a different provider fixture onto this row
  - the Prediction row backing the 68% figure, so the raw lambdas/Elo
    inputs are visible rather than just the final probability

No writes. Safe to run against production.

    python scripts/diagnose_sabah_dortmund.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.db.models import Match, Prediction, Team
from app.db.session import SessionLocal

CANDIDATE_NAMES = ["Sabah FA", "Sabah", "Borussia Dortmund", "Dortmund"]


def _print_team(db, team: Team) -> None:
    print(f"  Team #{team.id}: {team.name!r}  league={team.league!r}  country={team.country!r}")
    print(f"    aliases: {team.aliases!r}")
    matches = db.execute(
        select(Match)
        .where((Match.home_team_id == team.id) | (Match.away_team_id == team.id))
        .order_by(Match.date.asc())
    ).scalars().all()
    print(f"    {len(matches)} match row(s) total")
    by_league: dict[str, int] = {}
    for m in matches:
        by_league[m.league] = by_league.get(m.league, 0) + 1
    for league, count in sorted(by_league.items(), key=lambda kv: -kv[1]):
        print(f"      {league}: {count}")


def main() -> None:
    db = SessionLocal()
    try:
        print("=== Team rows ===")
        teams: dict[str, Team] = {}
        for name in CANDIDATE_NAMES:
            rows = db.execute(select(Team).where(Team.name == name)).scalars().all()
            if not rows:
                print(f"  (no Team row named {name!r})")
                continue
            for team in rows:
                teams[f"{team.id}"] = team
                _print_team(db, team)

        print("\n=== UEFA Champions League match(es) pairing Sabah with Dortmund ===")
        ucl = db.execute(
            select(Match).where(Match.league == "UEFA Champions League")
        ).scalars().all()
        hits = [
            m for m in ucl
            if ("sabah" in (m.home_team.name.lower() if m.home_team else "")
                or "sabah" in (m.away_team.name.lower() if m.away_team else "")
                or "dortmund" in (m.home_team.name.lower() if m.home_team else "")
                or "dortmund" in (m.away_team.name.lower() if m.away_team else ""))
        ]
        if not hits:
            print("  none found")
        for m in hits:
            print(f"\n  Match #{m.id}")
            print(f"    {m.home_team.name} (id {m.home_team_id}) vs {m.away_team.name} (id {m.away_team_id})")
            print(f"    league={m.league!r}  season={m.season!r}  status={m.status}  date={m.date}")
            print(f"    source={m.source!r}  api_fixture_id={m.api_fixture_id}")
            print(f"    score: {m.home_score}-{m.away_score}  ht: {m.ht_home_score}-{m.ht_away_score}")

            preds = db.execute(
                select(Prediction).where(Prediction.match_id == m.id).order_by(Prediction.created_at.desc())
            ).scalars().all()
            print(f"    {len(preds)} prediction row(s)")
            for p in preds[:3]:
                print(
                    f"      created_at={p.created_at}  model_version={p.model_version!r}  "
                    f"p_home={p.home_win:.3f}  p_draw={p.draw:.3f}  p_away={p.away_win:.3f}  "
                    f"confidence={p.confidence!r}  data_quality={p.data_quality_score:.3f}  "
                    f"model_agreement={p.model_agreement_score:.3f}"
                )
                print(f"        most_likely_score={p.most_likely_score!r} ({p.most_likely_score_probability:.3f})")
                print(f"        model_breakdown={p.model_breakdown!r}")

            if m.api_fixture_id is not None:
                siblings = db.execute(
                    select(Match).where(
                        Match.api_fixture_id == m.api_fixture_id, Match.id != m.id
                    )
                ).scalars().all()
                if siblings:
                    print(f"    *** {len(siblings)} OTHER match row(s) share this api_fixture_id: ***")
                    for s in siblings:
                        print(f"      Match #{s.id}: {s.home_team.name} vs {s.away_team.name} ({s.league}, {s.date})")
    finally:
        db.close()


if __name__ == "__main__":
    main()
