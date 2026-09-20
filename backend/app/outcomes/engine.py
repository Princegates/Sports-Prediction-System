"""Global Most-Likely Outcome Engine (spec section 26).

Given a populated Outcome Registry, the engine is genuinely simple:
``argmax(probability)``. The sophistication lives entirely upstream, in
making sure the registry only contains outcomes whose data-quality gate has
passed (spec section 27) -- the engine itself just has to not get that part
wrong.
"""

from __future__ import annotations

from app.outcomes.registry import Outcome


def select_global_most_likely(outcomes: list[Outcome]) -> Outcome | None:
    if not outcomes:
        return None
    return max(outcomes, key=lambda o: o.probability)


def top_n(outcomes: list[Outcome], n: int = 6) -> list[Outcome]:
    return sorted(outcomes, key=lambda o: o.probability, reverse=True)[:n]


def secondary_outcomes(outcomes: list[Outcome], *, exclude: tuple[str, str], n: int = 2) -> list[Outcome]:
    """The next-best outcomes after the headline pick -- "what else does the
    model like about this match", each standing on its own probability.

    Double Chance is excluded outright, not merely allowed to lose on
    probability. It's a union built from the very outcome it would sit next
    to (Home/Draw = P(home win) + P(draw)), so it is *always* at least as
    high as the 1X2 pick beneath it and would otherwise fill this list every
    time without adding anything -- a second opinion that is really the
    first one restated more loosely. HT Double Chance is the identical
    construction one level down (a union of HT Result) and is excluded for
    the same reason.
    """

    excluded_groups = ("double_chance", "ht_double_chance")
    candidates = [
        o for o in outcomes
        if o.mutually_exclusive_group not in excluded_groups and (o.market, o.selection) != exclude
    ]
    return top_n(candidates, n)
