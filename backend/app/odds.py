"""Market odds, and whether the model actually beats them.

Accuracy answers "were we right". It cannot answer the question that decides
whether any of this is worth paying for: *were we right about something the
market had wrong*. A 72% home win is worth nothing if the bookmaker also says
72% -- that is not an edge, that is agreement, and agreement is free.

Two things have to be right for the comparison to mean anything.

**The overround.** Decimal odds of 2.00 / 3.50 / 4.00 imply 50% + 28.6% + 25% =
103.6%. That extra 3.6 points is the bookmaker's margin, not a claim about
football. Comparing a model probability against the raw implied number would
credit the model with an edge on every single selection, which is exactly
backwards -- the margin is against you. It has to be removed first.

**Direction.** Edge is model minus market. Positive means the model thinks the
outcome is likelier than the price says, which is the only case where a bet
could have value. Negative is the model agreeing the price is generous to the
bookmaker.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MarketPrice:
    selection: str
    decimal_odds: float

    @property
    def raw_implied(self) -> float:
        """Before removing the margin. Sums to more than 1 across a market."""

        return 1.0 / self.decimal_odds if self.decimal_odds > 0 else 0.0


def overround(prices: list[MarketPrice]) -> float:
    """How much more than 100% the market's implied probabilities sum to.

    Typically 1.03-1.08 for a three-way football market. Below 1.0 would be
    arbitrage, which in practice means bad data rather than free money.
    """

    return sum(p.raw_implied for p in prices)


def fair_probabilities(prices: list[MarketPrice]) -> dict[str, float]:
    """Implied probabilities with the bookmaker's margin divided out.

    Proportional normalisation -- each selection keeps its share. It is the
    standard approach and it is not perfect: real margin is applied more
    heavily to longshots than favourites, so this slightly under-corrects the
    favourite and over-corrects the outsider. Modelling that properly (Shin,
    or power methods) needs assumptions this does not have; dividing through is
    honest about being approximate rather than precise about being wrong.
    """

    total = overround(prices)
    if total <= 0:
        return {}
    return {p.selection: p.raw_implied / total for p in prices}


def edge(model_probability: float, fair_probability: float) -> float:
    """Model minus market, in probability points. Positive favours the model."""

    return model_probability - fair_probability


def expected_value(model_probability: float, decimal_odds: float) -> float:
    """Expected return per unit staked, using the model's probability.

    Positive means the model considers the price better than fair. This is the
    figure that matters for value, and it is *conditional on the model being
    right* -- which is the whole question. It is a claim about the model's
    opinion, never a promise about the match.
    """

    return model_probability * decimal_odds - 1.0


@dataclass(frozen=True)
class ValueAssessment:
    selection: str
    model_probability: float
    decimal_odds: float
    fair_probability: float
    edge: float
    expected_value: float


def assess(model_probabilities: dict[str, float], prices: list[MarketPrice]) -> list[ValueAssessment]:
    """Compare a model's probabilities against a priced market.

    Only selections present in both are returned -- a price with no model
    opinion, or an opinion with no price, cannot be compared and guessing
    either side would invent the result.
    """

    fair = fair_probabilities(prices)
    by_selection = {p.selection: p for p in prices}

    assessments = []
    for selection, model_p in model_probabilities.items():
        price = by_selection.get(selection)
        if price is None or selection not in fair:
            continue
        assessments.append(
            ValueAssessment(
                selection=selection,
                model_probability=model_p,
                decimal_odds=price.decimal_odds,
                fair_probability=fair[selection],
                edge=edge(model_p, fair[selection]),
                expected_value=expected_value(model_p, price.decimal_odds),
            )
        )
    return sorted(assessments, key=lambda a: -a.edge)
