"""Live / in-play prediction engine (spec sections 29-32).

Without a paid live-stats feed (shot maps, live xG, etc.) the honest way to
do in-play prediction with zero-cost inputs is a time-scaled Poisson
projection: take the pre-match expected goals for each team, scale them down
by the fraction of the match remaining, and combine that with the goals
already on the board. It is deliberately simple and documented as such --
plugging in a richer live-stats provider later only means replacing
``_project_remaining_goals``, everything downstream (the recalculation
trigger, the Global Outcome Engine call, persistence) stays the same.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from app.db.models import LivePrediction, Match
from app.outcomes.engine import select_global_most_likely
from app.outcomes.registry import build_outcome_registry
from app.prediction_models.poisson_model import build_score_matrix, expected_goals

MAX_REMAINING_GOALS = 6

# Heuristic multipliers applied to a team's remaining-goal expectancy after a
# red card. These are placeholders pending a properly fitted red-card model
# (spec section 43 flags exactly this kind of thing for "model review").
RED_CARD_PENALIZED_MULTIPLIER = 0.75
RED_CARD_BENEFICIARY_MULTIPLIER = 1.20

TRIGGER_EVENTS = {
    "goal",
    "red_card_home",
    "red_card_away",
    "penalty",
    "var_decision",
    "substitution",
    "half_time",
    "match_restart",
    "kickoff",
    # A recurring re-sync against a real provider's current state, rather
    # than a discrete in-match event -- carries no red-card-style multiplier
    # of its own, it just re-projects from wherever the score/minute now are.
    "sync",
}


def _project_remaining_goals(
    db: Session,
    match: Match,
    minute: int,
    trigger_event: str,
) -> tuple[float, float]:
    lambda_home_full, lambda_away_full = expected_goals(db, match.home_team_id, match.away_team_id, match.league, match.date)

    remaining_minutes = max(90 - minute, 0)
    remaining_fraction = remaining_minutes / 90.0

    lambda_home_remaining = lambda_home_full * remaining_fraction
    lambda_away_remaining = lambda_away_full * remaining_fraction

    if trigger_event == "red_card_home":
        lambda_home_remaining *= RED_CARD_PENALIZED_MULTIPLIER
        lambda_away_remaining *= RED_CARD_BENEFICIARY_MULTIPLIER
    elif trigger_event == "red_card_away":
        lambda_away_remaining *= RED_CARD_PENALIZED_MULTIPLIER
        lambda_home_remaining *= RED_CARD_BENEFICIARY_MULTIPLIER

    return max(lambda_home_remaining, 0.02), max(lambda_away_remaining, 0.02)


def predict_live(
    db: Session,
    match: Match,
    minute: int,
    score_home: int,
    score_away: int,
    trigger_event: str = "goal",
) -> dict:
    lambda_home_remaining, lambda_away_remaining = _project_remaining_goals(db, match, minute, trigger_event)
    remaining_matrix = build_score_matrix(lambda_home_remaining, lambda_away_remaining, max_goals=MAX_REMAINING_GOALS)

    home_win = draw = away_win = 0.0
    btts_yes = 0.0
    goal_lines = [0.5, 1.5, 2.5, 3.5, 4.5]
    over_totals = {str(line): 0.0 for line in goal_lines}

    for rh in range(MAX_REMAINING_GOALS + 1):
        for ra in range(MAX_REMAINING_GOALS + 1):
            p = remaining_matrix[rh][ra]
            final_home = score_home + rh
            final_away = score_away + ra
            if final_home > final_away:
                home_win += p
            elif final_home == final_away:
                draw += p
            else:
                away_win += p
            if final_home >= 1 and final_away >= 1:
                btts_yes += p
            total_goals = final_home + final_away
            for line in goal_lines:
                if total_goals > line:
                    over_totals[str(line)] += p

    def clamp01(x: float) -> float:
        return min(1.0, max(0.0, x))

    home_win, draw, away_win = clamp01(home_win), clamp01(draw), clamp01(away_win)
    btts_yes = clamp01(btts_yes)
    over_totals = {k: clamp01(v) for k, v in over_totals.items()}

    matches_home = _rough_history_count(db, match.home_team_id, match.league, match.date)
    matches_away = _rough_history_count(db, match.away_team_id, match.league, match.date)

    outcomes = build_outcome_registry(
        home_win,
        draw,
        away_win,
        over_totals,
        btts_yes,
        1 - btts_yes,
        {},  # correct-score not meaningful for live re-projection here
        matches_available=min(matches_home, matches_away),
    )
    global_outcome = select_global_most_likely(outcomes)

    return {
        "home_win": home_win,
        "draw": draw,
        "away_win": away_win,
        "over_probabilities": over_totals,
        "btts_yes": btts_yes,
        "global_outcome_market": global_outcome.market if global_outcome else "Insufficient Data",
        "global_outcome_selection": global_outcome.selection if global_outcome else "Not enough match history yet",
        "global_outcome_probability": global_outcome.probability if global_outcome else 0.0,
    }


def _rough_history_count(db: Session, team_id: int, league: str, as_of: dt.datetime) -> int:
    from app.features.team_stats import matches_played_before

    return matches_played_before(db, team_id, as_of, league)


def record_live_event(
    db: Session,
    match: Match,
    minute: int,
    score_home: int,
    score_away: int,
    trigger_event: str,
) -> LivePrediction:
    if trigger_event not in TRIGGER_EVENTS:
        raise ValueError(f"Unknown trigger_event {trigger_event!r}; expected one of {sorted(TRIGGER_EVENTS)}")

    projection = predict_live(db, match, minute, score_home, score_away, trigger_event)

    live_prediction = LivePrediction(
        match_id=match.id,
        minute=minute,
        score_home=score_home,
        score_away=score_away,
        home_win=projection["home_win"],
        draw=projection["draw"],
        away_win=projection["away_win"],
        over_probabilities=projection["over_probabilities"],
        btts_yes=projection["btts_yes"],
        global_outcome_market=projection["global_outcome_market"],
        global_outcome_selection=projection["global_outcome_selection"],
        global_outcome_probability=projection["global_outcome_probability"],
        trigger_event=trigger_event,
    )
    db.add(live_prediction)

    match.status = "LIVE" if minute < 90 else "FINISHED"
    match.home_score = score_home
    match.away_score = score_away

    db.commit()
    db.refresh(live_prediction)
    return live_prediction
