"""The "X or Y" bets, in the form SportyBet quotes them.

Each is its own market with a Yes/No choice -- "Home Team or Over 2.5: Yes" wins
if the home side wins OR the match has more than 2.5 goals (a 0-3 away win
still wins it, because the second condition alone is enough). That differs from
the "&" combos (Result & Total Goals), which need both. Names are SportyBet's own,
including its uneven "Home Team" / "Away" / "Away Team" wording.

One table drives both the probabilities (registry.py) and settlement
(grading.py), so the two cannot drift apart. No dependencies, so either can
import it cheaply.
"""

from __future__ import annotations

# market name -> (the result that wins it, the other condition that also wins it)
RESULT_OR_MARKETS: dict[str, tuple[str, str]] = {
    "Home Team or Over 2.5": ("Home", "over_2.5"),
    "Home Team or Under 2.5": ("Home", "under_2.5"),
    "Draw or Over 2.5": ("Draw", "over_2.5"),
    "Draw or Under 2.5": ("Draw", "under_2.5"),
    "Away or Over 2.5": ("Away", "over_2.5"),
    "Away or Under 2.5": ("Away", "under_2.5"),
    "Home Team or GG": ("Home", "gg"),
    "Draw or GG": ("Draw", "gg"),
    "Away Team or GG": ("Away", "gg"),
    "Home Team or Any Clean Sheet": ("Home", "any_clean_sheet"),
    "Draw or Any Clean Sheet": ("Draw", "any_clean_sheet"),
    "Away Team or Any Clean Sheet": ("Away", "any_clean_sheet"),
}

# Plain-English form of each condition, for a market's definition text.
CONDITION_PHRASE = {
    "over_2.5": "total match goals are over 2.5",
    "under_2.5": "total match goals are under 2.5",
    "gg": "both teams score (GG)",
    "any_clean_sheet": "at least one team keeps a clean sheet (the match is not both-teams-to-score)",
}
SIDE_PHRASE = {"Home": "the home team wins", "Away": "the away team wins", "Draw": "the match is drawn"}


def group_for(market: str) -> str:
    """The registry group a market's Yes/No pair belongs to."""

    return "result_or_" + market.lower().replace(" ", "_")


RESULT_OR_GROUPS = frozenset(group_for(m) for m in RESULT_OR_MARKETS)


def wins(market: str, home_goals: int, away_goals: int) -> bool:
    """Whether "Yes" on this market wins at this scoreline."""

    side, condition = RESULT_OR_MARKETS[market]
    result = "Home" if home_goals > away_goals else ("Away" if home_goals < away_goals else "Draw")
    if result == side:
        return True
    total = home_goals + away_goals
    if condition == "over_2.5":
        return total > 2.5
    if condition == "under_2.5":
        return total < 2.5
    if condition == "gg":
        return home_goals > 0 and away_goals > 0
    return home_goals == 0 or away_goals == 0  # any_clean_sheet
