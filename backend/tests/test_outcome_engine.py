from app.outcomes.engine import select_global_most_likely, top_n
from app.outcomes.registry import build_outcome_registry


def _sample_registry(matches_available=20):
    return build_outcome_registry(
        home_win=0.55,
        draw=0.25,
        away_win=0.20,
        over_probabilities={"0.5": 0.94, "1.5": 0.78, "2.5": 0.52, "3.5": 0.30, "4.5": 0.15},
        btts_yes=0.58,
        btts_no=0.42,
        correct_score_probabilities={"2-1": 0.12, "1-0": 0.15, "1-1": 0.10},
        matches_available=matches_available,
    )


def test_global_most_likely_is_the_max_probability_outcome():
    outcomes = _sample_registry()
    winner = select_global_most_likely(outcomes)
    assert winner is not None
    assert winner.selection == "Over 0.5"
    assert winner.probability == 0.94


def test_registry_respects_mutually_exclusive_groups():
    outcomes = _sample_registry()
    group_1x2 = [o for o in outcomes if o.mutually_exclusive_group == "1x2"]
    assert {o.selection for o in group_1x2} == {"Home Win", "Draw", "Away Win"}
    assert abs(sum(o.probability for o in group_1x2) - 1.0) < 1e-9

    group_ou = [o for o in outcomes if o.mutually_exclusive_group == "ou_2.5"]
    assert abs(sum(o.probability for o in group_ou) - 1.0) < 1e-9


def test_low_data_quality_gates_out_high_min_requirement_outcomes():
    outcomes = build_outcome_registry(
        home_win=0.5,
        draw=0.3,
        away_win=0.2,
        over_probabilities={"0.5": 0.9},
        btts_yes=0.5,
        btts_no=0.5,
        correct_score_probabilities={"1-0": 0.9},  # deliberately inflated, min_data_requirement=10
        matches_available=7,  # enough for 1x2/OU/BTTS (min 5), not enough for Correct Score (min 10)
    )
    # Correct Score requires 10 matches of history; with only 3 available it
    # must not appear (and therefore cannot be wrongly selected as global
    # most-likely outcome even though its probability looks huge here).
    assert all(o.market != "Correct Score" for o in outcomes)
    winner = select_global_most_likely(outcomes)
    assert winner is not None
    assert winner.market != "Correct Score"


def test_top_n_orders_descending():
    outcomes = _sample_registry()
    ranked = top_n(outcomes, n=3)
    assert len(ranked) == 3
    assert ranked[0].probability >= ranked[1].probability >= ranked[2].probability
