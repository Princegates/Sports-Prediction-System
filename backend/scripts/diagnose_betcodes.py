#!/usr/bin/env python3
"""Read-only: run the exact query pipeline /api/betcodes/preview runs
(app.betcode.selection.build_candidate_legs / select_legs) against the real
database, printing where the funnel empties -- scheduled matches in the
window, how many of those have a Prediction row, how many have a MatchOdds
row, and how many have both plus a priced outcome at the requested accuracy
floor. Changes nothing.

    python scripts/diagnose_betcodes.py --league "English Premier League" \\
        --days-ahead 3 --min-probability 0.5
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.betcode.selection import SlipCriteria, build_candidate_legs, select_legs
from app.db.models import Match, MatchOdds, Prediction
from app.db.session import SessionLocal
from app.outcomes.registry import outcomes_from_prediction


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--league", default=None)
    parser.add_argument("--days-ahead", type=int, default=3)
    parser.add_argument("--min-probability", type=float, default=0.5)
    parser.add_argument("--target-odds", type=float, default=3.0)
    parser.add_argument("--bookmaker", default="Bet365")
    args = parser.parse_args()

    sys.stdout.reconfigure(line_buffering=True)

    db = SessionLocal()
    try:
        now = dt.datetime.utcnow()
        cutoff = now + dt.timedelta(days=args.days_ahead)
        print(f"Window: {now:%Y-%m-%d %H:%M} .. {cutoff:%Y-%m-%d %H:%M} UTC  (league={args.league or 'ANY'})\n")

        match_query = select(Match).where(Match.date >= now, Match.date < cutoff, Match.status == "SCHEDULED")
        if args.league:
            match_query = match_query.where(Match.league == args.league)
        matches = list(db.execute(match_query).scalars())
        print(f"1. SCHEDULED matches in window: {len(matches)}")
        for m in matches[:15]:
            print(f"     #{m.id:<6} {m.date:%Y-%m-%d %H:%M}  home={m.home_team_id} away={m.away_team_id}  league={m.league!r}")
        if len(matches) > 15:
            print(f"     ... and {len(matches) - 15} more")

        match_ids = [m.id for m in matches]
        if not match_ids:
            print("\nNo scheduled matches at all in this window/league -- nothing downstream can work.")
            return

        pred_rows = list(db.execute(select(Prediction).where(Prediction.match_id.in_(match_ids))).scalars())
        pred_match_ids = {p.match_id for p in pred_rows}
        print(f"\n2. Of those, matches WITH a Prediction row: {len(pred_match_ids)} / {len(matches)}")

        odds_rows = list(db.execute(select(MatchOdds).where(MatchOdds.match_id.in_(match_ids))).scalars())
        odds_match_ids = {o.match_id for o in odds_rows}
        print(f"3. Of those, matches WITH a MatchOdds row (any bookmaker/market): {len(odds_match_ids)} / {len(matches)}")
        bookmakers = sorted({o.bookmaker for o in odds_rows})
        print(f"   Bookmakers present in this window: {bookmakers or 'none'}")

        both = pred_match_ids & odds_match_ids
        print(f"\n4. Matches with BOTH a Prediction and a MatchOdds row: {len(both)} / {len(matches)}")
        if pred_match_ids and odds_match_ids and not both:
            print("   *** Predictions and odds exist for DIFFERENT matches -- disjoint sets. ***")
            print("   Sample prediction-only match ids:", sorted(pred_match_ids - odds_match_ids)[:10])
            print("   Sample odds-only match ids:      ", sorted(odds_match_ids - pred_match_ids)[:10])

        if both:
            print("\n4b. Per-match (market, selection) comparison -- stored odds vs. model outcomes >= floor:")
            pred_by_match = {p.match_id: p for p in pred_rows}
            odds_by_match: dict[int, list[MatchOdds]] = {}
            for o in odds_rows:
                odds_by_match.setdefault(o.match_id, []).append(o)
            for mid in sorted(both)[:8]:
                stored_pairs = sorted({(o.market, o.selection) for o in odds_by_match[mid]})
                prediction = pred_by_match[mid]
                all_outcomes = outcomes_from_prediction(prediction)
                model_pairs = sorted(
                    (o.market, o.selection, round(o.probability, 3))
                    for o in all_outcomes
                    if o.probability >= args.min_probability
                )
                overlap = {(m, s) for (m, s) in stored_pairs} & {(m, s) for (m, s, _p) in model_pairs}
                matches_available = round((prediction.data_quality_score or 0.0) * 10)
                print(f"   match #{mid}:")
                print(
                    f"     prediction row: home_win={prediction.home_win:.3f} draw={prediction.draw:.3f} "
                    f"away_win={prediction.away_win:.3f} btts_yes={prediction.btts_yes:.3f} "
                    f"btts_no={prediction.btts_no:.3f} data_quality_score={prediction.data_quality_score!r} "
                    f"-> matches_available={matches_available}"
                )
                print(f"     outcomes_from_prediction() total outcomes (any probability): {len(all_outcomes)}")
                if all_outcomes:
                    top5 = sorted(all_outcomes, key=lambda o: -o.probability)[:5]
                    print(f"     top 5 by probability: {[(o.market, o.selection, round(o.probability, 3)) for o in top5]}")
                print(f"     stored odds markets:  {stored_pairs}")
                print(f"     model outcomes >= {args.min_probability:.0%}: {model_pairs}")
                print(f"     overlap: {sorted(overlap) or 'NONE'}")

        print(f"\n5. Running build_candidate_legs() with min_probability={args.min_probability:.0%}, any market, any price_bookmaker ...")
        criteria = SlipCriteria(
            bookmaker=args.bookmaker,
            target_odds=args.target_odds,
            markets=(),
            min_probability=args.min_probability,
            league=args.league,
            days_ahead=args.days_ahead,
        )
        legs = build_candidate_legs(db, criteria)
        print(f"   Candidate legs returned: {len(legs)}")
        for leg in legs[:15]:
            print(
                f"     match#{leg.match_id} {leg.home_team} vs {leg.away_team}  "
                f"{leg.market}/{leg.selection}  p={leg.model_probability:.0%}  odds={leg.decimal_odds} ({leg.priced_by})"
            )

        print("\n6. Full select_legs() result (what the API actually returns):")
        result = select_legs(db, criteria)
        print(f"   legs={len(result.legs)}  combined_odds={result.combined_odds:.2f}  "
              f"combined_probability={result.combined_probability:.0%}  met_target={result.met_target}")
        for w in result.warnings:
            print(f"   warning: {w}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
