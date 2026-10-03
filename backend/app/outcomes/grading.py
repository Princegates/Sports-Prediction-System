"""Grades a (market, selection) pair against a match's real final score.

This is deliberately separate from ``registry.py``, which only computes
*probabilities* for the outcome registry -- never whether one came true.
Settling a pick (AdminPick, BookingSlip, ...) needs the opposite direction:
given what actually happened, was this specific selection correct?

Covers the markets an admin or member is actually likely to pick: 1X2,
Double Chance, Draw No Bet, Both Teams To Score, Total/Home/Away Goals
Over/Under at any line, Correct Score, Clean Sheets, Odd/Even, and the
half-time equivalents of the above (when half-time scores were recorded).
Anything outside that set -- combo markets like "Result & BTTS", Winning
Margin, Multigoals, HT/FT -- returns ``None`` ("can't auto-grade this")
rather than guessing, so a pick in one of those stays pending until a
Super Admin resolves it by hand.
"""

from __future__ import annotations

import re

_OU_RE = re.compile(r"^(?:HT )?(?:Total|Home|Away) Goals (\d+(?:\.\d+)?)$")


def _result_1x2(home: int, away: int) -> str:
    return "H" if home > away else ("A" if home < away else "D")


def grade_outcome(
    market: str,
    selection: str,
    *,
    home_score: int,
    away_score: int,
    ht_home_score: int | None = None,
    ht_away_score: int | None = None,
) -> bool | None:
    """True/False once gradable, ``None`` when this market isn't covered or
    the half-time score it needs was never recorded."""

    result = _result_1x2(home_score, away_score)

    if market == "Match Result":
        return {"Home Win": "H", "Draw": "D", "Away Win": "A"}.get(selection) == result

    if market == "Double Chance":
        pairs = {"Home/Draw": {"H", "D"}, "Home/Away": {"H", "A"}, "Draw/Away": {"D", "A"}}
        group = pairs.get(selection)
        return result in group if group is not None else None

    if market == "Draw No Bet":
        if result == "D":
            return None  # void/push -- not a win or loss for either side
        return {"Home": "H", "Away": "A"}.get(selection) == result

    if market == "Both Teams To Score":
        both_scored = home_score > 0 and away_score > 0
        if selection == "Yes":
            return both_scored
        if selection == "No":
            return not both_scored
        return None

    if market == "Correct Score":
        return selection == f"{home_score}-{away_score}"

    if market in ("Home Clean Sheet", "Away Clean Sheet", "Both Teams Clean Sheet"):
        clean = {
            "Home Clean Sheet": away_score == 0,
            "Away Clean Sheet": home_score == 0,
            "Both Teams Clean Sheet": home_score == 0 and away_score == 0,
        }[market]
        if selection == "Yes":
            return clean
        if selection == "No":
            return not clean
        return None

    if market in ("Total Goals Odd/Even", "Home Goals Odd/Even", "Away Goals Odd/Even"):
        total = {
            "Total Goals Odd/Even": home_score + away_score,
            "Home Goals Odd/Even": home_score,
            "Away Goals Odd/Even": away_score,
        }[market]
        is_even = total % 2 == 0
        if selection == "Even":
            return is_even
        if selection == "Odd":
            return not is_even
        return None

    ou_match = _OU_RE.match(market)
    if ou_match:
        line = float(ou_match.group(1))
        if market.startswith("HT "):
            if ht_home_score is None or ht_away_score is None:
                return None
            total = ht_home_score + ht_away_score if market.startswith("HT Total") else (
                ht_home_score if "Home" in market else ht_away_score
            )
        else:
            total = home_score + away_score if market.startswith("Total") else (
                home_score if market.startswith("Home") else away_score
            )
        if selection.startswith("Over "):
            return total > line
        if selection.startswith("Under "):
            return total < line
        return None

    if market == "HT Correct Score":
        if ht_home_score is None or ht_away_score is None:
            return None
        return selection == f"{ht_home_score}-{ht_away_score}"

    if market in ("HT Result", "HT Double Chance"):
        if ht_home_score is None or ht_away_score is None:
            return None
        ht_result = _result_1x2(ht_home_score, ht_away_score)
        if market == "HT Result":
            return {"Home": "H", "Draw": "D", "Away": "A"}.get(selection) == ht_result
        pairs = {"Home/Draw": {"H", "D"}, "Home/Away": {"H", "A"}, "Draw/Away": {"D", "A"}}
        group = pairs.get(selection)
        return ht_result in group if group is not None else None

    if market == "HT Both Teams To Score":
        if ht_home_score is None or ht_away_score is None:
            return None
        both_scored = ht_home_score > 0 and ht_away_score > 0
        if selection == "Yes":
            return both_scored
        if selection == "No":
            return not both_scored
        return None

    return None
