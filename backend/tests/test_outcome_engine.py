from types import SimpleNamespace

from app.outcomes.engine import select_global_most_likely, top_n
from app.outcomes.registry import build_outcome_registry, matrix_derived_outcomes, outcomes_from_prediction


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


# --- matrix-derived markets (winning margin, clean sheets, parity, etc.) ---


def test_every_matrix_derived_group_sums_to_one():
    """The correctness property that matters most here: every new market is
    a genuine partition of the outcome space, not a cherry-picked subset that
    would make the browse page's "these add up to 100%" label a lie."""

    outcomes = matrix_derived_outcomes(lambda_home=1.6, lambda_away=1.1, matches_available=20)
    assert outcomes, "expected a non-empty registry"

    groups: dict[str, float] = {}
    for o in outcomes:
        groups[o.mutually_exclusive_group] = groups.get(o.mutually_exclusive_group, 0.0) + o.probability
    for group, total in groups.items():
        assert abs(total - 1.0) < 1e-9, f"group {group!r} summed to {total}, not 1.0"


def test_matrix_derived_outcomes_respect_the_data_gate():
    assert matrix_derived_outcomes(lambda_home=1.4, lambda_away=1.2, matches_available=3) == []


def test_both_clean_sheet_cannot_exceed_either_single_clean_sheet():
    outcomes = matrix_derived_outcomes(lambda_home=1.5, lambda_away=1.2, matches_available=20)
    by_key = {(o.market, o.selection): o.probability for o in outcomes}
    both = by_key[("Both Teams Clean Sheet", "Yes")]
    assert both <= by_key[("Home Clean Sheet", "Yes")] + 1e-9
    assert both <= by_key[("Away Clean Sheet", "Yes")] + 1e-9


def _prediction_stub(**overrides) -> SimpleNamespace:
    base = dict(
        home_win=0.5,
        draw=0.3,
        away_win=0.2,
        over_probabilities={"2.5": 0.55},
        btts_yes=0.5,
        btts_no=0.5,
        correct_score_probabilities={"1-0": 0.12},
        data_quality_score=1.0,
        model_breakdown={},
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_outcomes_from_prediction_adds_matrix_markets_when_lambdas_are_present():
    prediction = _prediction_stub(model_breakdown={"poisson": {"lambda_home": 1.6, "lambda_away": 1.1}})
    outcomes = outcomes_from_prediction(prediction)
    assert any(o.market == "Winning Margin" for o in outcomes)
    assert any(o.market == "Home Clean Sheet" for o in outcomes)


def test_outcomes_from_prediction_skips_matrix_markets_without_lambdas():
    """Old predictions, and the test fixtures across this codebase, persist
    an empty ``model_breakdown`` -- the browse page must degrade to exactly
    what it offered before this feature, not error."""

    prediction = _prediction_stub(model_breakdown={})
    outcomes = outcomes_from_prediction(prediction)
    assert not any(o.market == "Winning Margin" for o in outcomes)
    assert any(o.market == "Match Result" for o in outcomes)  # the base registry still works
