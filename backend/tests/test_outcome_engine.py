from types import SimpleNamespace

import pytest

from app.outcomes.engine import secondary_outcomes, select_global_most_likely, top_n
from app.outcomes.registry import (
    DOMINANT_UNION_GROUPS,
    build_outcome_registry,
    ht_ft_joint_outcomes,
    ht_matrix_derived_outcomes,
    matrix_derived_outcomes,
    outcomes_from_prediction,
)


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


# --- secondary outcomes: "2 more" alongside the headline pick --------------


def test_secondary_outcomes_never_repeats_the_headline_pick():
    outcomes = _sample_registry()
    winner = select_global_most_likely(outcomes)
    also = secondary_outcomes(outcomes, exclude=(winner.market, winner.selection))
    assert all((o.market, o.selection) != (winner.market, winner.selection) for o in also)


def test_secondary_outcomes_excludes_double_chance():
    """Double Chance is a union of the 1X2 pick it would sit beside -- always
    at least as high, and therefore never a second opinion, only the first
    one restated more loosely. Built so the 55%+25%=80% Home/Draw selection
    would otherwise dominate this list every time."""

    outcomes = _sample_registry()  # home_win=0.55, draw=0.25 -> Home/Draw=0.80
    also = secondary_outcomes(outcomes, exclude=("Total Goals 0.5", "Over 0.5"))
    assert all(o.mutually_exclusive_group != "double_chance" for o in also)


def test_secondary_outcomes_are_the_next_highest_by_probability():
    outcomes = _sample_registry()
    winner = select_global_most_likely(outcomes)
    also = secondary_outcomes(outcomes, exclude=(winner.market, winner.selection), n=2)

    assert len(also) == 2
    assert also[0].probability >= also[1].probability
    # Nothing outside this pair -- excluding the headline and Double Chance
    # -- may outrank what was chosen.
    remaining = [
        o for o in outcomes
        if o.mutually_exclusive_group != "double_chance" and (o.market, o.selection) != (winner.market, winner.selection)
    ]
    top_remaining = sorted(remaining, key=lambda o: -o.probability)[:2]
    assert [o.probability for o in also] == [o.probability for o in top_remaining]


def test_secondary_outcomes_shrinks_gracefully_for_thin_data():
    """A match too thin for Correct Score (min_data_requirement=10) never
    surfaces one here -- never an error, never padding with something the
    data-quality gate would have refused for the headline pick too."""

    outcomes = build_outcome_registry(
        home_win=0.5, draw=0.3, away_win=0.2,
        over_probabilities={}, btts_yes=0.5, btts_no=0.5,
        correct_score_probabilities={"1-0": 0.9},  # inflated and gated out at this data level
        matches_available=5,  # enough for 1x2/BTTS (min 5), not Correct Score (min 10)
    )
    winner = select_global_most_likely(outcomes)
    also = secondary_outcomes(outcomes, exclude=(winner.market, winner.selection))
    assert len(also) <= 2
    assert all(o.market != "Correct Score" for o in also)


def test_secondary_outcomes_of_an_empty_registry_is_empty():
    assert secondary_outcomes([], exclude=("Match Result", "Home Win")) == []


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


# --- half-time markets -------------------------------------------------


def test_every_ht_matrix_derived_group_sums_to_one():
    """Same correctness property as the full-time matrix markets: every HT
    group is a genuine partition, not a cherry-picked subset -- except
    ht_double_chance, three overlapping unions of HT Result that by
    construction sum to 2 (same as FT Double Chance always has), and
    ht_correct_score, which is only the top-8 scorelines by design (same as
    FT Correct Score) rather than every possible one."""

    outcomes = ht_matrix_derived_outcomes(lambda_home_ht=0.7, lambda_away_ht=0.5, matches_available=20)
    assert outcomes, "expected a non-empty HT registry"

    groups: dict[str, float] = {}
    for o in outcomes:
        groups[o.mutually_exclusive_group] = groups.get(o.mutually_exclusive_group, 0.0) + o.probability
    for group, total in groups.items():
        if group == "ht_double_chance":
            assert abs(total - 2.0) < 1e-9, f"group {group!r} summed to {total}, not 2.0"
            continue
        if group == "ht_correct_score":
            assert 0.0 < total <= 1.0, f"group {group!r} summed to {total}, outside (0, 1]"
            continue
        assert abs(total - 1.0) < 1e-9, f"group {group!r} summed to {total}, not 1.0"


def test_ht_double_chance_is_a_union_of_ht_result():
    outcomes = ht_matrix_derived_outcomes(lambda_home_ht=0.7, lambda_away_ht=0.5, matches_available=20)
    by_key = {(o.market, o.selection): o.probability for o in outcomes}

    home = by_key[("HT Result", "Home")]
    draw = by_key[("HT Result", "Draw")]
    away = by_key[("HT Result", "Away")]
    assert by_key[("HT Double Chance", "Home/Draw")] == pytest.approx(home + draw)
    assert by_key[("HT Double Chance", "Home/Away")] == pytest.approx(home + away)
    assert by_key[("HT Double Chance", "Draw/Away")] == pytest.approx(draw + away)


def test_ht_lambdas_are_lower_than_ft_lambdas_would_produce_more_goals():
    """A sanity check on the whole premise: a match with a modest HT
    expectation should draw the bulk of its correct-score mass toward
    low-scoring half-time lines, not the higher totals a full 90-minute
    lambda of the same size would produce."""

    low = ht_matrix_derived_outcomes(lambda_home_ht=0.4, lambda_away_ht=0.3, matches_available=20)
    by_key = {(o.market, o.selection): o.probability for o in low}
    assert by_key[("HT Correct Score", "0-0")] > by_key.get(("HT Correct Score", "2-2"), 0.0)


def test_ht_matrix_derived_outcomes_respects_the_data_gate():
    assert ht_matrix_derived_outcomes(lambda_home_ht=0.6, lambda_away_ht=0.5, matches_available=3) == []


def test_outcomes_from_prediction_adds_ht_markets_when_ht_lambdas_are_present():
    prediction = _prediction_stub(
        model_breakdown={
            "poisson": {"lambda_home": 1.6, "lambda_away": 1.1, "lambda_home_ht": 0.7, "lambda_away_ht": 0.5}
        }
    )
    outcomes = outcomes_from_prediction(prediction)
    assert any(o.market == "HT Result" for o in outcomes)
    assert any(o.market == "HT Correct Score" for o in outcomes)


def test_outcomes_from_prediction_skips_ht_markets_without_ht_lambdas():
    """A prediction generated before this feature shipped has FT lambdas but
    no HT ones -- it must keep working exactly as before, not error."""

    prediction = _prediction_stub(model_breakdown={"poisson": {"lambda_home": 1.6, "lambda_away": 1.1}})
    outcomes = outcomes_from_prediction(prediction)
    assert not any(o.market == "HT Result" for o in outcomes)
    assert any(o.market == "Winning Margin" for o in outcomes)  # FT matrix markets still work


def test_secondary_outcomes_excludes_ht_double_chance():
    prediction = _prediction_stub(
        model_breakdown={
            "poisson": {"lambda_home": 1.6, "lambda_away": 1.1, "lambda_home_ht": 0.7, "lambda_away_ht": 0.5}
        }
    )
    outcomes = outcomes_from_prediction(prediction)
    winner = select_global_most_likely(outcomes)
    also = secondary_outcomes(outcomes, exclude=(winner.market, winner.selection), n=len(outcomes))
    assert all(o.mutually_exclusive_group != "ht_double_chance" for o in also)


# --- HT/FT and every combo built on top of it ----------------------------


def _ht_ft(**overrides) -> list:
    params = dict(lambda_home=1.6, lambda_away=1.1, lambda_home_ht=0.7, lambda_away_ht=0.5, matches_available=20)
    params.update(overrides)
    return ht_ft_joint_outcomes(**params)


def test_every_ht_ft_group_is_a_genuine_partition_except_the_dc_unions():
    """Same property test_every_ht_matrix_derived_group_sums_to_one already
    runs for the plain HT markets, extended to every joint HT+FT combo:
    each one must be a real partition of probability (sums to 1) unless it's
    built from a Double Chance union, which by construction does not."""

    outcomes = _ht_ft()
    assert outcomes, "expected a non-empty HT/FT registry"

    groups: dict[str, float] = {}
    counts: dict[str, int] = {}
    for o in outcomes:
        groups[o.mutually_exclusive_group] = groups.get(o.mutually_exclusive_group, 0.0) + o.probability
        counts[o.mutually_exclusive_group] = counts.get(o.mutually_exclusive_group, 0) + 1

    for group, total in groups.items():
        if group in DOMINANT_UNION_GROUPS:
            assert total > 1.0, f"union group {group!r} summed to {total}, expected > 1.0"
            continue
        assert abs(total - 1.0) < 1e-9, f"group {group!r} summed to {total}, not 1.0"

    assert counts["ht_ft"] <= 9
    assert counts["half_most_goals"] <= 3


def test_ht_ft_is_a_nine_way_grid_of_ht_result_by_ft_result():
    outcomes = _ht_ft()
    by_selection = {o.selection: o.probability for o in outcomes if o.market == "HT/FT"}
    assert set(by_selection) <= {f"{h}/{f}" for h in "1X2" for f in "1X2"}
    assert abs(sum(by_selection.values()) - 1.0) < 1e-9


def test_ht_ft_double_chance_is_a_union_and_excluded_from_secondary_outcomes():
    prediction = _prediction_stub(
        model_breakdown={
            "poisson": {"lambda_home": 1.6, "lambda_away": 1.1, "lambda_home_ht": 0.7, "lambda_away_ht": 0.5}
        }
    )
    outcomes = outcomes_from_prediction(prediction)
    assert any(o.mutually_exclusive_group == "ht_ft_double_chance" for o in outcomes)
    assert any(o.mutually_exclusive_group == "ht_double_chance_btts" for o in outcomes)

    winner = select_global_most_likely(outcomes)
    also = secondary_outcomes(outcomes, exclude=(winner.market, winner.selection), n=len(outcomes))
    assert all(o.mutually_exclusive_group != "ht_ft_double_chance" for o in also)
    assert all(o.mutually_exclusive_group != "ht_double_chance_btts" for o in also)


def test_half_with_most_goals_matches_a_direct_comparison():
    """Cross-check against a hand-computed answer for a lopsided case: a
    much higher first-half rate than second-half rate should make "1st
    Half" the clear favorite, not "2nd Half" or "Equal"."""

    outcomes = _ht_ft(lambda_home=1.2, lambda_away=0.9, lambda_home_ht=1.0, lambda_away_ht=0.8)
    by_key = {o.selection: o.probability for o in outcomes if o.market == "Half With Most Goals"}
    assert by_key["1st Half"] > by_key["2nd Half"]
    assert by_key["1st Half"] > by_key["Equal"]


def test_ht_2h_result_marginals_match_ht_result_and_2h_result():
    """The joint HT+2H Result grid's own marginals must agree with the plain
    HT Result outcomes already produced elsewhere -- same underlying HT
    matrix, so summing out the second half must reproduce it exactly."""

    ht_outcomes = ht_matrix_derived_outcomes(lambda_home_ht=0.7, lambda_away_ht=0.5, matches_available=20)
    ht_result = {o.selection: o.probability for o in ht_outcomes if o.market == "HT Result"}

    joint = [o for o in _ht_ft() if o.market == "HT + 2H Result"]
    marginal: dict[str, float] = {}
    for o in joint:
        ht_side = o.selection.split(" & ")[0]
        marginal[ht_side] = marginal.get(ht_side, 0.0) + o.probability

    for side in ("Home", "Draw", "Away"):
        assert marginal[side] == pytest.approx(ht_result[side], abs=1e-9)


def test_ht_ft_joint_outcomes_respects_the_data_gate():
    assert ht_ft_joint_outcomes(
        lambda_home=1.6, lambda_away=1.1, lambda_home_ht=0.7, lambda_away_ht=0.5, matches_available=3,
    ) == []


def test_outcomes_from_prediction_adds_ht_ft_markets_when_all_lambdas_are_present():
    prediction = _prediction_stub(
        model_breakdown={
            "poisson": {"lambda_home": 1.6, "lambda_away": 1.1, "lambda_home_ht": 0.7, "lambda_away_ht": 0.5}
        }
    )
    outcomes = outcomes_from_prediction(prediction)
    assert any(o.market == "HT/FT" for o in outcomes)
    assert any(o.market == "Half With Most Goals" for o in outcomes)


def test_outcomes_from_prediction_skips_ht_ft_markets_without_ht_lambdas():
    prediction = _prediction_stub(model_breakdown={"poisson": {"lambda_home": 1.6, "lambda_away": 1.1}})
    outcomes = outcomes_from_prediction(prediction)
    assert not any(o.market == "HT/FT" for o in outcomes)
