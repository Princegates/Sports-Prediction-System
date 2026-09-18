#!/usr/bin/env python3
"""Did the model beat the market?

Accuracy says whether calls were right. It cannot say whether they were right
about something the market had wrong, and only the second one is worth
selling. A 72% home win that the bookmaker also priced at 72% is not insight,
it is agreement, and agreement is available free.

This replays finished matches that have stored odds, generates the prediction
the model *would* have made before kickoff -- same leakage-free path the
backtester uses, nothing after ``as_of`` -- strips the bookmaker's margin from
the price, and compares.

Three figures come out, in increasing order of how much they mean:

**Mean edge** is how far the model's probability sits from the market's fair
one. Positive is necessary but nowhere near sufficient; a model can be
systematically overconfident and show a cheerful positive edge while losing.

**Hit rate on +EV selections** asks whether the calls the model thought were
underpriced actually landed more often than their price implied.

**Return** is the only one that answers the question. Flat stake on every
selection the model rated above its fair price, settled at the odds actually
offered. Above zero means the model found something the market missed, over
this sample. Below zero means it did not, however good the accuracy looked.

    python scripts/measure_edge.py
    python scripts/measure_edge.py --min-edge 0.05 --league "English Premier League"
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.db.migrate import init_db
from app.db.models import Match, MatchOdds
from app.db.session import SessionLocal, engine
from app.model_store import load_calibrators, load_ensemble_weights, load_ml_model
from app.odds import MarketPrice, assess
from app.prediction_models.ensemble import generate_prediction
from app.prediction_models.ml_model import LeagueFeatureCache

SELECTIONS = ("Home Win", "Draw", "Away Win")


def actual_selection(match: Match) -> str | None:
    if match.home_score is None or match.away_score is None:
        return None
    if match.home_score > match.away_score:
        return "Home Win"
    if match.home_score < match.away_score:
        return "Away Win"
    return "Draw"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--league", default=None, help="Restrict to one league")
    parser.add_argument("--min-edge", type=float, default=0.02,
                        help="Only stake selections the model rates this far above fair price")
    parser.add_argument("--bookmaker", default=None, help="Use one bookmaker's prices only")
    args = parser.parse_args()

    init_db(engine)
    db = SessionLocal()
    try:
        odds_rows = list(db.execute(select(MatchOdds)).scalars())
        if args.bookmaker:
            odds_rows = [o for o in odds_rows if o.bookmaker == args.bookmaker]
        if not odds_rows:
            print("No stored odds. Run scripts/import_historical_odds.py first.", file=sys.stderr)
            raise SystemExit(1)

        # Best price per selection per match: if several bookmakers are stored,
        # taking the best is what a bettor would actually get, and using a
        # random one would understate the market rather than represent it.
        best: dict[int, dict[str, float]] = defaultdict(dict)
        for row in odds_rows:
            current = best[row.match_id].get(row.selection)
            if current is None or row.decimal_odds > current:
                best[row.match_id][row.selection] = row.decimal_odds

        matches = {
            m.id: m
            for m in db.execute(select(Match).where(Match.id.in_(list(best)))).scalars()
            if m.home_score is not None
        }
        if args.league:
            matches = {i: m for i, m in matches.items() if m.league == args.league}
        if not matches:
            print("No finished matches with odds yet.", file=sys.stderr)
            raise SystemExit(1)

        by_league: dict[str, list[Match]] = defaultdict(list)
        for match in matches.values():
            by_league[match.league].append(match)

        print(f"Replaying {len(matches)} finished matches with odds, across {len(by_league)} league(s).")
        print(f"Staking every selection rated at least {args.min_edge:.0%} above its fair price.\n")

        staked = wins = 0
        returned = 0.0
        # Every settled stake's return, kept so the result can carry an
        # uncertainty rather than a bare point estimate.
        settled: list[float] = []
        edges: list[float] = []
        per_league: dict[str, list[float]] = defaultdict(list)

        for league, league_matches in sorted(by_league.items()):
            model = load_ml_model(league)
            calibrators = load_calibrators(league)
            weights = load_ensemble_weights(league)
            cache = LeagueFeatureCache(db, league)

            for match in sorted(league_matches, key=lambda m: m.date):
                result = generate_prediction(
                    db, match.home_team_id, match.away_team_id, league, match.date,
                    ml_model=model, calibrators=calibrators, weights=weights, feature_cache=cache,
                )
                model_probabilities = {
                    "Home Win": result.home_win, "Draw": result.draw, "Away Win": result.away_win,
                }
                prices = [
                    MarketPrice(selection, best[match.id][selection])
                    for selection in SELECTIONS
                    if selection in best[match.id]
                ]
                if len(prices) < 3:
                    continue  # an incomplete market cannot have its margin removed

                landed = actual_selection(match)
                for assessment in assess(model_probabilities, prices):
                    edges.append(assessment.edge)
                    per_league[league].append(assessment.edge)
                    if assessment.edge < args.min_edge:
                        continue
                    staked += 1
                    if assessment.selection == landed:
                        wins += 1
                        returned += assessment.decimal_odds
                        settled.append(assessment.decimal_odds - 1.0)
                    else:
                        settled.append(-1.0)
        print("=" * 64)
        if not edges:
            print("Nothing comparable -- no match had a complete three-way market.")
            raise SystemExit(1)

        print(f"Mean edge across every selection : {sum(edges) / len(edges):+.2%}")
        for league in sorted(per_league):
            values = per_league[league]
            print(f"  {league:<26} {sum(values) / len(values):+.2%}  ({len(values)} selections)")

        print()
        if not staked:
            print(f"No selection cleared the {args.min_edge:.0%} edge threshold.")
            print("The model never disagreed with the market by that much -- which is itself")
            print("an answer: on this sample it is tracking the price, not beating it.")
            return

        profit = returned - staked
        per_stake = profit / staked

        # Betting returns are extremely noisy: a losing strategy shows a profit
        # over a few hundred stakes often enough that a point estimate alone is
        # close to meaningless. Validating this script against a market built to
        # mirror the model exactly -- unbeatable by construction, guaranteed to
        # lose the margin -- produced +7% over 180 stakes. Without an interval
        # that reads as an edge. With one it reads as noise, which is what it is.
        mean = sum(settled) / len(settled)
        variance = sum((x - mean) ** 2 for x in settled) / max(len(settled) - 1, 1)
        standard_error = (variance / len(settled)) ** 0.5
        low, high = mean - 1.96 * standard_error, mean + 1.96 * standard_error

        print(f"Selections staked  : {staked}")
        print(f"Won                : {wins}  ({wins / staked:.1%})")
        print(f"Return             : {returned:.2f} from {staked:.0f} staked")
        print(f"Profit per stake   : {per_stake:+.2%}")
        print(f"95% interval       : {low:+.2%} to {high:+.2%}")
        print()

        if low > 0:
            print("Profitable, and the interval clears zero. That is a real result on this")
            print("sample -- still worth repeating on fresh matches before trusting it.")
        elif high < 0:
            print("Losing, and the interval does not reach zero. The accuracy figure is")
            print("real, but the model is not beating the price: it broadly agrees with the")
            print("market and pays the margin for the privilege. More data would not fix")
            print("that; a better model might.")
        else:
            needed = int((1.96 * variance ** 0.5 / max(abs(mean), 0.01)) ** 2)
            print("Inconclusive -- the interval spans zero, so this sample cannot tell an")
            print("edge from luck, whichever way the headline number points.")
            print(f"At this variance, separating a {abs(mean):.1%} effect from noise needs on the")
            print(f"order of {needed:,} stakes. Collect more seasons before concluding anything.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
