import math
from types import SimpleNamespace

import pytest

from app.outcomes.engine import secondary_outcomes, select_global_most_likely, top_n
from app.outcomes.registry import (
    DOMINANT_UNION_GROUPS,
    build_outcome_registry,
    corners_outcomes,
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
    would make the browse page's "these add up to 100%" label a lie --
    except the dominant-union combos (Double Chance & BTTS/Total Goals),
    which are deliberately unions of each other the same way plain Double
    Chance already is (DOMINANT_UNION_GROUPS), and never claim to sum to 1."""

    outcomes = matrix_derived_outcomes(lambda_home=1.6, lambda_away=1.1, matches_available=20)
    assert outcomes, "expected a non-empty registry"

    groups: dict[str, float] = {}
    for o in outcomes:
        groups[o.mutually_exclusive_group] = groups.get(o.mutually_exclusive_group, 0.0) + o.probability
    for group, total in groups.items():
        if group in DOMINANT_UNION_GROUPS:
            continue
        assert abs(total - 1.0) < 1e-9, f"group {group!r} summed to {total}, not 1.0"


def test_matrix_derived_outcomes_respect_the_data_gate():
    assert matrix_derived_outcomes(lambda_home=1.4, lambda_away=1.2, matches_available=3) == []


def test_both_clean_sheet_cannot_exceed_either_single_clean_sheet():
    outcomes = matrix_derived_outcomes(lambda_home=1.5, lambda_away=1.2, matches_available=20)
    by_key = {(o.market, o.selection): o.probability for o in outcomes}
    both = by_key[("Both Teams Clean Sheet", "Yes")]
    assert both <= by_key[("Home Clean Sheet", "Yes")] + 1e-9
    assert both <= by_key[("Away Clean Sheet", "Yes")] + 1e-9


def test_handicap_markets_exist_for_lines_up_to_two_goals_only():
    outcomes = matrix_derived_outcomes(lambda_home=1.6, lambda_away=1.1, matches_available=20)
    markets = {o.market for o in outcomes}
    # Nothing wider than two goals either way (product rule).
    assert not {f"Handicap {line}" for line in ("-5", "-4", "-3", "+3", "+4", "+5")} & markets
    for line in ("-2", "-1", "+1", "+2"):
        assert f"Handicap {line}" in markets
        selections = {o.selection for o in outcomes if o.market == f"Handicap {line}"}
        assert selections == {"Home", "Draw", "Away"}


def test_handicap_minus_one_matches_a_direct_scoreline_comparison():
    """Independent recomputation from the raw score matrix, so a sign or
    off-by-one error in the registry's own bucketing can't mark itself
    correct."""

    from app.prediction_models.poisson_model import build_score_matrix

    lambda_home, lambda_away = 1.6, 1.1
    matrix = build_score_matrix(lambda_home, lambda_away)
    max_goals = len(matrix) - 1

    expected = {"Home": 0.0, "Draw": 0.0, "Away": 0.0}
    for h in range(max_goals + 1):
        for a in range(max_goals + 1):
            adjusted_margin = (h - 1) - a
            key = "Home" if adjusted_margin > 0 else ("Away" if adjusted_margin < 0 else "Draw")
            expected[key] += matrix[h][a]

    outcomes = matrix_derived_outcomes(lambda_home=lambda_home, lambda_away=lambda_away, matches_available=20)
    by_selection = {o.selection: o.probability for o in outcomes if o.market == "Handicap -1"}
    for selection, prob in expected.items():
        assert by_selection[selection] == pytest.approx(prob)


def test_handicap_home_covers_less_often_on_a_tougher_line():
    """Covering Handicap -2 (win by 3+) can never be more likely than
    covering the easier Handicap -1 (win by 2+) -- the favourite's goal
    output doesn't change, only how much of it the line demands. The
    mirror image holds for Away covering the positive lines."""

    outcomes = matrix_derived_outcomes(lambda_home=1.6, lambda_away=1.1, matches_available=20)
    by_key = {(o.market, o.selection): o.probability for o in outcomes}
    negative_lines = ["-1", "-2"]
    for easier, tougher in zip(negative_lines, negative_lines[1:]):
        assert by_key[(f"Handicap {tougher}", "Home")] <= by_key[(f"Handicap {easier}", "Home")] + 1e-9

    positive_lines = ["+1", "+2"]
    for easier, tougher in zip(positive_lines, positive_lines[1:]):
        assert by_key[(f"Handicap {tougher}", "Away")] <= by_key[(f"Handicap {easier}", "Away")] + 1e-9


def test_handicap_minus_one_equals_the_matching_winning_margin_buckets():
    """Handicap -1 ("home must win by 2+ to cover") and Winning Margin are
    built from the same matrix by entirely separate code paths above; their
    buckets have to agree. Home covering -1 means a margin of exactly 2, 3,
    or 4+; the push (Draw) is a margin of exactly 1; Away covering it is
    everything else -- a drawn or lost match plus a 1-goal home win."""

    outcomes = matrix_derived_outcomes(lambda_home=1.6, lambda_away=1.1, matches_available=20)
    by_key = {(o.market, o.selection): o.probability for o in outcomes}

    home_by_2_or_more = (
        by_key[("Winning Margin", "Home by exactly 2")]
        + by_key[("Winning Margin", "Home by exactly 3")]
        + by_key[("Winning Margin", "Home by 4+")]
    )
    away_or_draw_or_home_by_1 = (
        by_key[("Winning Margin", "Draw")]
        + by_key[("Winning Margin", "Away by exactly 1")]
        + by_key[("Winning Margin", "Away by exactly 2")]
        + by_key[("Winning Margin", "Away by exactly 3")]
        + by_key[("Winning Margin", "Away by 4+")]
    )

    assert by_key[("Handicap -1", "Home")] == pytest.approx(home_by_2_or_more)
    assert by_key[("Handicap -1", "Draw")] == pytest.approx(by_key[("Winning Margin", "Home by exactly 1")])
    assert by_key[("Handicap -1", "Away")] == pytest.approx(away_or_draw_or_home_by_1)


def test_asian_handicap_markets_exist_for_the_standard_half_lines():
    outcomes = matrix_derived_outcomes(lambda_home=1.6, lambda_away=1.1, matches_available=20)
    markets = {o.market for o in outcomes}
    assert not {f"Asian Handicap {line}" for line in ("-3.5", "-2.5", "+2.5", "+3.5")} & markets
    for line in ("-1.5", "-0.5", "+0.5", "+1.5"):
        assert f"Asian Handicap {line}" in markets
        selections = {o.selection for o in outcomes if o.market == f"Asian Handicap {line}"}
        assert selections == {"Home", "Away"}


def test_asian_handicap_minus_half_matches_a_direct_scoreline_comparison():
    """A half-integer line never lands exactly on 0, so this is always a
    clean 2-way split -- no push bucket exists or is needed, unlike the
    3-way integer Handicap above."""

    from app.prediction_models.poisson_model import build_score_matrix

    lambda_home, lambda_away = 1.6, 1.1
    matrix = build_score_matrix(lambda_home, lambda_away)
    max_goals = len(matrix) - 1

    expected_home = sum(
        matrix[h][a] for h in range(max_goals + 1) for a in range(max_goals + 1) if (h - 0.5) - a > 0
    )

    outcomes = matrix_derived_outcomes(lambda_home=lambda_home, lambda_away=lambda_away, matches_available=20)
    by_selection = {o.selection: o.probability for o in outcomes if o.market == "Asian Handicap -0.5"}
    assert by_selection["Home"] == pytest.approx(expected_home)
    assert by_selection["Away"] == pytest.approx(1 - expected_home)


def test_asian_handicap_home_covers_less_often_on_a_tougher_line():
    outcomes = matrix_derived_outcomes(lambda_home=1.6, lambda_away=1.1, matches_available=20)
    by_key = {(o.market, o.selection): o.probability for o in outcomes}
    negative_lines = ["-0.5", "-1.5"]
    for easier, tougher in zip(negative_lines, negative_lines[1:]):
        assert by_key[(f"Asian Handicap {tougher}", "Home")] <= by_key[(f"Asian Handicap {easier}", "Home")] + 1e-9


def test_home_and_away_no_bet_exclude_the_voided_result():
    """Home No Bet never offers "Home" as a selection (that's the result it
    voids) and its two real selections' odds come from the same home/draw/
    away split Draw No Bet already uses -- just normalized by excluding a
    different one of the three."""

    outcomes = matrix_derived_outcomes(lambda_home=1.6, lambda_away=1.1, matches_available=20)
    by_key = {(o.market, o.selection): o.probability for o in outcomes}

    assert {o.selection for o in outcomes if o.market == "Home No Bet"} == {"Draw", "Away"}
    assert {o.selection for o in outcomes if o.market == "Away No Bet"} == {"Home", "Draw"}

    # Home No Bet's two selections, and Away No Bet's two, each sum to 1.
    assert by_key[("Home No Bet", "Draw")] + by_key[("Home No Bet", "Away")] == pytest.approx(1.0)
    assert by_key[("Away No Bet", "Home")] + by_key[("Away No Bet", "Draw")] == pytest.approx(1.0)


def test_which_team_to_score_matches_clean_sheet_buckets():
    """Only Home = the home side scores, away doesn't -- the away side's
    own clean sheet, minus the double-blank case. Both = neither team's
    clean sheet holds."""

    outcomes = matrix_derived_outcomes(lambda_home=1.6, lambda_away=1.1, matches_available=20)
    by_key = {(o.market, o.selection): o.probability for o in outcomes}

    wts = {o.selection: o.probability for o in outcomes if o.market == "Which Team To Score"}
    assert sum(wts.values()) == pytest.approx(1.0)

    none_p = by_key[("Both Teams Clean Sheet", "Yes")]  # 0-0
    away_cs = by_key[("Away Clean Sheet", "Yes")]  # home fails to score, i.e. P(home=0)
    home_cs = by_key[("Home Clean Sheet", "Yes")]  # away fails to score, i.e. P(away=0)
    assert wts["None"] == pytest.approx(none_p)
    # Only Home scores: home>0 and away=0 -> P(away=0) minus the double-blank case.
    assert wts["Only Home"] == pytest.approx(home_cs - none_p)
    # Only Away scores: away>0 and home=0 -> P(home=0) minus the double-blank case.
    assert wts["Only Away"] == pytest.approx(away_cs - none_p)
    assert wts["Both"] == pytest.approx(1 - home_cs - away_cs + none_p)


def test_goal_range_coarse_bands_match_a_direct_scoreline_comparison():
    from app.prediction_models.poisson_model import build_score_matrix

    lambda_home, lambda_away = 1.6, 1.1
    matrix = build_score_matrix(lambda_home, lambda_away)
    max_goals = len(matrix) - 1

    expected = {"0-1": 0.0, "2-3": 0.0, "4-6": 0.0, "7+": 0.0}
    for h in range(max_goals + 1):
        for a in range(max_goals + 1):
            total = h + a
            key = "0-1" if total <= 1 else ("2-3" if total <= 3 else ("4-6" if total <= 6 else "7+"))
            expected[key] += matrix[h][a]

    outcomes = matrix_derived_outcomes(lambda_home=lambda_home, lambda_away=lambda_away, matches_available=20)
    by_selection = {o.selection: o.probability for o in outcomes if o.market == "Goal Range"}
    for k, v in expected.items():
        assert by_selection[k] == pytest.approx(v)


def test_double_chance_and_total_goals_is_the_matching_result_union():
    outcomes = matrix_derived_outcomes(lambda_home=1.6, lambda_away=1.1, matches_available=20)
    by_key = {(o.market, o.selection): o.probability for o in outcomes}

    home_over = by_key[("Result & Total Goals 2.5", "Home & Over 2.5")]
    draw_over = by_key[("Result & Total Goals 2.5", "Draw & Over 2.5")]
    assert by_key[("Double Chance & Total Goals 2.5", "Home/Draw & Over 2.5")] == pytest.approx(home_over + draw_over)


def test_double_chance_and_btts_is_the_matching_result_union():
    outcomes = matrix_derived_outcomes(lambda_home=1.6, lambda_away=1.1, matches_available=20)
    by_key = {(o.market, o.selection): o.probability for o in outcomes}

    home_yes = by_key[("Result & BTTS", "Home & BTTS Yes")]
    away_yes = by_key[("Result & BTTS", "Away & BTTS Yes")]
    assert by_key[("Double Chance & BTTS", "Home/Away & BTTS Yes")] == pytest.approx(home_yes + away_yes)


def test_dc_combo_groups_are_excluded_from_the_full_partition_check():
    """These are dominant unions of Result & BTTS / Result & Total Goals,
    the same shape plain Double Chance already is -- DOMINANT_UNION_GROUPS
    says so, and they're excluded from test_every_matrix_derived_group_sums_to_one above."""

    outcomes = matrix_derived_outcomes(lambda_home=1.6, lambda_away=1.1, matches_available=20)
    groups = {o.mutually_exclusive_group for o in outcomes if o.market == "Double Chance & BTTS"}
    assert groups == {"dc_btts"}
    assert "dc_btts" in DOMINANT_UNION_GROUPS
    assert "dc_total_goals_2.5" in DOMINANT_UNION_GROUPS


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


def test_win_both_halves_matches_the_matching_ht_plus_2h_result_cell():
    """"Home Team To Win Both Halves" is just one specific cell of the same
    HT + 2H Result joint grid, re-sliced to a Yes/No question -- not a new
    computation, so it has to equal that cell exactly."""

    outcomes = _ht_ft()
    joint = {o.selection: o.probability for o in outcomes if o.market == "HT + 2H Result"}
    both = {o.selection: o.probability for o in outcomes if o.market == "Home Team To Win Both Halves"}
    assert both["Yes"] == pytest.approx(joint["Home & Home"])
    assert both["Yes"] + both["No"] == pytest.approx(1.0)

    away_both = {o.selection: o.probability for o in outcomes if o.market == "Away Team To Win Both Halves"}
    assert away_both["Yes"] == pytest.approx(joint["Away & Away"])


def test_win_either_half_is_at_least_win_both_halves():
    """"Either half" is a superset of "both halves" -- winning both trivially
    satisfies "at least one" -- so it can never be the smaller probability."""

    outcomes = _ht_ft()
    by_key = {(o.market, o.selection): o.probability for o in outcomes}
    assert by_key[("Home Team To Win Either Half", "Yes")] >= by_key[("Home Team To Win Both Halves", "Yes")] - 1e-9
    assert by_key[("Away Team To Win Either Half", "Yes")] >= by_key[("Away Team To Win Both Halves", "Yes")] - 1e-9


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


# --- corners markets ---------------------------------------------------


def test_every_corners_group_sums_to_one():
    outcomes = corners_outcomes(lambda_home_corners=6.0, lambda_away_corners=4.5, matches_available=20)
    assert outcomes, "expected a non-empty registry"

    groups: dict[str, float] = {}
    for o in outcomes:
        groups[o.mutually_exclusive_group] = groups.get(o.mutually_exclusive_group, 0.0) + o.probability
    for group, total in groups.items():
        assert abs(total - 1.0) < 1e-9, f"group {group!r} summed to {total}, not 1.0"


def test_corners_outcomes_respect_the_data_gate():
    assert corners_outcomes(lambda_home_corners=6.0, lambda_away_corners=4.5, matches_available=3) == []


def test_corners_outcomes_favor_the_side_expected_to_win_more_corners():
    outcomes = corners_outcomes(lambda_home_corners=7.0, lambda_away_corners=3.0, matches_available=20)
    by_key = {(o.market, o.selection): o.probability for o in outcomes}
    assert by_key[("Most Corners", "Home")] > by_key[("Most Corners", "Away")]


def test_outcomes_from_prediction_adds_corners_markets_when_corners_lambdas_are_present():
    prediction = _prediction_stub(
        model_breakdown={
            "poisson": {"lambda_home": 1.6, "lambda_away": 1.1, "lambda_home_corners": 6.0, "lambda_away_corners": 4.5}
        }
    )
    outcomes = outcomes_from_prediction(prediction)
    assert any(o.market == "Most Corners" for o in outcomes)
    assert any(o.market.startswith("Total Corners") for o in outcomes)


def test_outcomes_from_prediction_skips_corners_markets_without_corners_lambdas():
    """A league enrich_match_stats.py hasn't been run for -- the common case
    today -- must not surface a Most Corners / Total Corners guess."""

    prediction = _prediction_stub(model_breakdown={"poisson": {"lambda_home": 1.6, "lambda_away": 1.1}})
    outcomes = outcomes_from_prediction(prediction)
    assert not any(o.market == "Most Corners" for o in outcomes)
    assert not any(o.market.startswith("Total Corners") for o in outcomes)


def test_second_half_total_goals_is_a_partition_per_line_and_the_remainder_of_the_match():
    outcomes = _ht_ft(lambda_home=1.6, lambda_away=1.1, lambda_home_ht=0.7, lambda_away_ht=0.5)
    by_key = {(o.market, o.selection): o for o in outcomes}

    for line in ("0.5", "1.5", "2.5", "3.5", "4.5"):
        over, under = by_key[(f"2H Total Goals {line}", f"Over {line}")], by_key[(f"2H Total Goals {line}", f"Under {line}")]
        assert over.probability + under.probability == pytest.approx(1.0)
        assert over.mutually_exclusive_group == under.mutually_exclusive_group

    # Second-half rate is 0.9 + 0.6 = 1.5 goals, so Over 0.5 sits near 1 - e^-1.5.
    assert by_key[("2H Total Goals 0.5", "Over 0.5")].probability == pytest.approx(1 - math.exp(-1.5), abs=0.02)
    # Higher lines are strictly harder to clear.
    overs = [by_key[(f"2H Total Goals {l}", f"Over {l}")].probability for l in ("0.5", "1.5", "2.5", "3.5", "4.5")]
    assert overs == sorted(overs, reverse=True)
