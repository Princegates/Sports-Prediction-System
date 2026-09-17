from app.prediction_models.poisson_model import build_score_matrix, markets_from_matrix


def test_score_matrix_sums_to_one():
    matrix = build_score_matrix(1.4, 1.1)
    total = sum(sum(row) for row in matrix)
    assert abs(total - 1.0) < 1e-6


def test_markets_are_internally_consistent():
    matrix = build_score_matrix(1.8, 0.7)
    markets = markets_from_matrix(matrix, 1.8, 0.7)

    assert abs(markets.home_win + markets.draw + markets.away_win - 1.0) < 1e-6
    assert abs(markets.btts_yes + markets.btts_no - 1.0) < 1e-9
    # Strong home side should be favored to win.
    assert markets.home_win > markets.away_win
    assert markets.most_likely_score_probability > 0
    assert "-" in markets.most_likely_score

    # Over probabilities should be monotonically decreasing as the line rises.
    lines = ["0.5", "1.5", "2.5", "3.5", "4.5"]
    values = [markets.over_probabilities[line] for line in lines]
    assert all(a >= b for a, b in zip(values, values[1:]))


def test_high_scoring_expectation_favors_higher_totals():
    low_matrix = build_score_matrix(0.6, 0.5)
    high_matrix = build_score_matrix(2.5, 2.2)
    low = markets_from_matrix(low_matrix, 0.6, 0.5)
    high = markets_from_matrix(high_matrix, 2.5, 2.2)

    assert high.over_probabilities["2.5"] > low.over_probabilities["2.5"]
