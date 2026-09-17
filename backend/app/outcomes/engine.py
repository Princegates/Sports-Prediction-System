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
