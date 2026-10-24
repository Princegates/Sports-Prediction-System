"""Grades a (market, selection) pair against a match's real final score.

This is deliberately separate from ``registry.py``, which only computes
*probabilities* for the outcome registry -- never whether one came true.
Settling a pick (AdminPick, BookingSlip, ...) needs the opposite direction:
given what actually happened, was this specific selection correct?

Covers the markets an admin or member is actually likely to pick: 1X2,
Double Chance, Draw No Bet, Both Teams To Score, Total/Home/Away Goals
Over/Under at any line, Correct Score, Clean Sheets, Odd/Even, and the
half-time and second-half equivalents of the above (when half-time scores
were recorded; the second half is full time less half time).
Anything outside that set -- combo markets like "Result & BTTS", Winning
Margin, Multigoals, HT/FT -- returns ``None`` ("can't auto-grade this")
rather than guessing, so a pick in one of those stays pending until a
Super Admin resolves it by hand.
"""

from __future__ import annotations

import re

from app.outcomes.registry import HT_OR_FT_MARKET
from app.outcomes.result_or import RESULT_OR_MARKETS, wins as result_or_wins

_OU_RE = re.compile(r"^(?:HT |2H )?(?:Total|Home|Away) Goals (\d+(?:\.\d+)?)$")


# The single-half markets besides Over/Under: "HT Result", "2H Double Chance", ...
_HALF_MARKET_RE = re.compile(r"^(HT|2H) (Result|Double Chance|Both Teams To Score|Correct Score)$")


def _half_score(
    market: str, home: int, away: int, ht_home: int | None, ht_away: int | None,
) -> tuple[int, int] | None:
    """The score of the half this market is about: the half-time score for
    "HT ...", full time less half time for "2H ...". None when half-time
    wasn't recorded."""

    if ht_home is None or ht_away is None:
        return None
    return (ht_home, ht_away) if market.startswith("HT ") else (home - ht_home, away - ht_away)


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

    if market in ("Home Win To Nil", "Away Win To Nil"):
        won = (result == "H" and away_score == 0) if market.startswith("Home") else (result == "A" and home_score == 0)
        if selection == "Yes":
            return won
        if selection == "No":
            return not won
        return None

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

    if market == HT_OR_FT_MARKET:
        # Wins if the side's result holds at half-time or at full time.
        if ht_home_score is None or ht_away_score is None:
            return None
        wanted = {"Home": "H", "Draw": "D", "Away": "A"}.get(selection)
        if wanted is None:
            return None
        return wanted in (_result_1x2(ht_home_score, ht_away_score), result)

    if market in RESULT_OR_MARKETS:
        # SportyBet's "Home Team or Over 2.5" etc.: Yes wins if either condition holds.
        if selection not in ("Yes", "No"):
            return None
        return result_or_wins(market, home_score, away_score) == (selection == "Yes")

    ou_match = _OU_RE.match(market)
    if ou_match:
        line = float(ou_match.group(1))
        if market.startswith(("HT ", "2H ")):
            half = _half_score(market, home_score, away_score, ht_home_score, ht_away_score)
            # Only the combined total is a 2nd-half market (no per-team 2H lines).
            if half is None or (market.startswith("2H ") and not market.startswith("2H Total")):
                return None
            total = half[0] + half[1] if "Total" in market else (half[0] if "Home" in market else half[1])
        else:
            total = home_score + away_score if market.startswith("Total") else (
                home_score if market.startswith("Home") else away_score
            )
        if selection.startswith("Over "):
            return total > line
        if selection.startswith("Under "):
            return total < line
        return None

    half_market = _HALF_MARKET_RE.match(market)
    if half_market:
        half = _half_score(market, home_score, away_score, ht_home_score, ht_away_score)
        if half is None:
            return None
        half_home, half_away = half
        kind = half_market.group(2)
        if kind == "Correct Score":
            return selection == f"{half_home}-{half_away}"
        half_result = _result_1x2(half_home, half_away)
        if kind == "Result":
            return {"Home": "H", "Draw": "D", "Away": "A"}.get(selection) == half_result
        if kind == "Double Chance":
            pairs = {"Home/Draw": {"H", "D"}, "Home/Away": {"H", "A"}, "Draw/Away": {"D", "A"}}
            group = pairs.get(selection)
            return half_result in group if group is not None else None
        both_scored = half_home > 0 and half_away > 0
        if selection == "Yes":
            return both_scored
        if selection == "No":
            return not both_scored
        return None

    return None
