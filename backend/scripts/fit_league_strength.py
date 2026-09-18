#!/usr/bin/env python3
"""Fit how the domestic leagues compare, from European results.

Elo is built one league at a time and is zero-sum, so every league averages
the same rating however strong it really is. That makes a Champions League
prediction between clubs from two leagues a comparison of two numbers that
were never on the same scale.

This fits one offset per league from matches that actually crossed leagues,
validates it on matches it was not fitted to, and stores it only if it helped.

    python scripts/fit_league_strength.py                 # report, store nothing
    python scripts/fit_league_strength.py --save          # store if it validates
    python scripts/fit_league_strength.py --save --force   # store regardless

Nothing here writes to predictions. Until the stored calibration is read by
the predictor, this is a measurement.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import SessionLocal
from app.prediction_models.league_strength import (
    bootstrap_draws,
    collect_bridges,
    fit,
    fit_and_validate,
    pairwise_intervals,
)

# Below this, the fit is arithmetic rather than evidence. Four free offsets
# from fifty matches would produce four confident numbers about nothing.
MIN_BRIDGES = 60


def _percentile(values: list[float], interval: float = 0.90) -> tuple[float, float]:
    if not values:
        return float("nan"), float("nan")
    lower = (1.0 - interval) / 2.0
    last = len(values) - 1
    return values[min(last, int(lower * len(values)))], values[min(last, int((1.0 - lower) * len(values)))]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--holdout-from", default=None,
                        help="ISO date; matches from here on are held out. Default: the last 20%%.")
    parser.add_argument("--draws", type=int, default=200, help="Bootstrap resamples for the intervals")
    parser.add_argument("--save", action="store_true", help="Store the calibration if it validates")
    parser.add_argument("--force", action="store_true", help="Store even if it does not validate")
    args = parser.parse_args()

    sys.stdout.reconfigure(line_buffering=True)

    db = SessionLocal()
    try:
        bridges = collect_bridges(db)
        print(f"Cross-league matches found: {len(bridges)}")

        if not bridges:
            print("\nNothing to fit. Import European seasons first:")
            print('    python scripts/import_api_football.py --leagues "UEFA Champions League" '
                  '"UEFA Europa League" --seasons 2021 2022 2023 2024 2025')
            raise SystemExit(1)

        pairs = Counter(
            tuple(sorted((b.home_league, b.away_league))) for b in bridges
        )
        print("\nBy league pair -- each pair's offset difference rests on these:")
        for (a, b), count in pairs.most_common():
            print(f"  {count:>4}  {a} vs {b}")

        unrated = [b for b in bridges if not (b.home_rated and b.away_rated)]
        if unrated:
            share = len(unrated) / len(bridges)
            print(f"\n  {len(unrated)} of {len(bridges)} ({share:.0%}) involve a club with no Elo")
            print("  history before kickoff, so it entered at the base rating. Those matches")
            print("  cannot separate league strength from the club's own unknown strength.")
            if share > 0.2:
                print("\n  That is too many to fit on. Rebuild Elo history for the domestic")
                print("  leagues first (the retrain mode of the refresh workflow), then re-run.")
                raise SystemExit(1)

        seasons = Counter(b.date.year for b in bridges)
        print("\nBy year:")
        for year, count in sorted(seasons.items()):
            print(f"  {year}  {count:>4}")

        if len(bridges) < MIN_BRIDGES:
            print(f"\nOnly {len(bridges)} matches. Below {MIN_BRIDGES} the offsets are arithmetic,")
            print("not evidence -- import more European seasons before trusting this.")
            raise SystemExit(1)

        cut = args.holdout_from
        if cut:
            holdout_from = dt.datetime.fromisoformat(cut)
        else:
            ordered = sorted(b.date for b in bridges)
            holdout_from = ordered[int(len(ordered) * 0.8)]

        result = fit_and_validate(bridges, holdout_from=holdout_from)
        print(f"\n=== Validation (holdout from {holdout_from:%Y-%m-%d}) ===")
        print(f"  fitted on        : {result.train_matches} matches")
        print(f"  held out         : {result.holdout_matches} matches")
        print(f"  log loss, fitted : {result.holdout_log_loss:.4f}")
        print(f"  log loss, no offsets: {result.baseline_log_loss:.4f}")
        print(f"  improvement      : {result.improvement:+.4f} "
              f"({'better' if result.improvement > 0 else 'worse'} out of sample)")

        full = fit(bridges)
        print(f"\n=== Offsets fitted on all {len(bridges)} matches ===")
        print(f"  Elo points, centred on zero. Only differences matter.\n")

        draws = bootstrap_draws(bridges, draws=args.draws)
        bounds = {
            league: _percentile(sorted(d[league] for d in draws if league in d))
            for league in full.offsets
        }
        for league, value in sorted(full.offsets.items(), key=lambda kv: -kv[1]):
            low, high = bounds.get(league, (float("nan"), float("nan")))
            straddles = low < 0 < high
            flag = "  cannot be told from average" if straddles else ""
            print(f"  {league:<26} {value:+7.1f}   90% interval [{low:+7.1f}, {high:+7.1f}]{flag}")

        print(f"\n  home advantage in these ties: {full.home_advantage:.1f} Elo points")

        print("\n=== The gaps a prediction actually uses ===\n")
        print("  A tie is scored on the difference between two offsets, never on either")
        print("  alone, and the two move together across resamples -- so a league that")
        print("  cannot be told from average can still sit a measured distance from a")
        print("  specific other league.\n")

        gaps = pairwise_intervals(draws)
        ranked_gaps = sorted(
            gaps.items(),
            key=lambda kv: -abs(full.offsets.get(kv[0][0], 0.0) - full.offsets.get(kv[0][1], 0.0)),
        )
        for (a, b), (low, high) in ranked_gaps:
            point = full.offsets.get(a, 0.0) - full.offsets.get(b, 0.0)
            verdict = "undetermined" if low < 0 < high else "measured"
            print(f"  {a:<24} - {b:<24} {point:+7.1f}   [{low:+7.1f}, {high:+7.1f}]  {verdict}")

        undetermined = [lg for lg, (lo, hi) in bounds.items() if lo < 0 < hi]
        if undetermined:
            print(f"\n  {len(undetermined)} of {len(bounds)} leagues cannot yet be placed relative to")
            print("  average. Their offsets are the best guess available, not a measurement.")

        if not args.save:
            print("\nNothing stored. Re-run with --save to store it.")
            return

        if not result.helps and not args.force:
            print("\nNOT STORED: the offsets did not improve held-out predictions.")
            print("That is a result, not a failure -- it says the European matches available")
            print("do not yet separate these leagues. --force stores it anyway.")
            raise SystemExit(1)

        full.save(db)
        print(f"\nStored. {len(full.offsets)} league offsets, fitted on {full.matches} matches.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
