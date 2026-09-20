"""Turns a set of preferences into a combination of real-priced legs.

This is the "AI Generation" step: someone picks a target combined price, a
minimum accuracy, and optionally which markets and bookmaker they want, and
this reads the database for matches that qualify and assembles them toward
that target.

``bookmaker`` and ``price_bookmaker`` answer two different questions, kept
separate on purpose. ``bookmaker`` is who the *code* is for -- it goes
straight to the aggregator at generation time and needs nothing from this
database. ``price_bookmaker`` is whose captured prices to build the combined
odds *from*, and defaults to none, meaning any bookmaker this project has a
real quote from. They will often be the same name, but odds are only ever
captured from whichever bookmaker(s) the data source actually returns --
Bet365 and similar, not necessarily the Ghanaian brands the aggregator step
targets -- and requiring an exact match between the two turned "preview my
selections" into "preview my selections, but only if I happen to have typed
the one bookmaker's name this project has prices for", which is a worse
feature than either half alone.

Four rules keep the result honest rather than merely impressive-looking:

**Real prices only.** A leg needs an actual ``MatchOdds`` row -- there is no
synthetic "implied odds" fallback. A combined price built partly from real
prices and partly from guesses would look identical to one built from real
prices throughout, and the difference matters enormously to whoever pastes
it into a betting app. Which bookmaker supplied each leg's price is recorded
on the leg itself, never hidden behind a single number that might quietly
mix bookmakers.

**One leg per match.** Two qualifying markets on the same fixture (a 70% Home
Win and a 68% BTTS No) are correlated, not independent, so only the stronger
one is used. This also keeps ``combined_probability`` a valid product of
independent events -- different matches don't influence each other, so
multiplying their probabilities is the correct thing to do, and would not be
if two legs ever shared a match.

**No forced fit.** The greedy search adds legs, safest first, until the
target is reached or there is nothing left that qualifies. It never swaps in
a weaker leg just to land closer to the number that was asked for -- a slip
is only as good as its worst leg, and a target price is a preference, not a
promise.

**Meaningful markets by default.** Leaving ``markets`` unset does not search
literally every priced line -- see ``DEFAULT_MARKETS`` for why an unfiltered
search would fill every leg with a barely-above-stake extreme Total Goals
line instead of anything a bettor would recognise as a pick. An unusual
market is still reachable by asking for it explicitly.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models import Match, MatchOdds, Prediction
from app.outcomes.registry import Outcome, find_outcome, outcomes_from_prediction

DEFAULT_MIN_PROBABILITY = 0.65
DEFAULT_MAX_LEGS = 8
DEFAULT_DAYS_AHEAD = 7

# What "any market" actually searches when criteria.markets is left empty.
#
# It is deliberately not literally every priced market. A bookmaker
# publishes a Total Goals line for nearly every half-integer and quarter
# line from 0.5 to 8.5+, and api_football_ingest captures all of them --
# which means an extreme line (Under 7.5, Under 8.5) clears 99% for almost
# every match, every time, and would win _best_priced_outcome's "highest
# probability" comparison against every other market on offer. That leg is
# correctly priced and correctly the single safest thing in the pool -- and
# worthless as a recommendation, since a bookmaker prices it barely above a
# stake-only return (odds ~1.01). "Any market" means any of the markets
# bettors actually compare, not literally any line this project happens to
# have a price for. An unusual line is still reachable -- just by asking for
# it explicitly via criteria.markets, the same as any other specific choice.
DEFAULT_MARKETS = (
    "Match Result",
    "Double Chance",
    "Both Teams To Score",
    "Draw No Bet",
    "Total Goals 1.5",
    "Total Goals 2.5",
    "Total Goals 3.5",
)


@dataclass(frozen=True)
class SlipCriteria:
    bookmaker: str                          # who the code is generated for
    target_odds: float
    markets: tuple[str, ...] = ()           # empty = DEFAULT_MARKETS; see its own comment for why
    min_probability: float = DEFAULT_MIN_PROBABILITY
    max_legs: int = DEFAULT_MAX_LEGS
    # Kept for existing single-league callers (the chat assistant resolves
    # exactly one league from text, never several) -- ignored whenever
    # `leagues` below is non-empty. New multi-league UI should set `leagues`
    # and leave this at its default.
    league: str | None = None
    # Empty = no league filter (every league this deployment has data for).
    # Non-empty = any one of these, same "empty means unfiltered" convention
    # `markets` already uses -- a match qualifies by being in ANY of the
    # leagues listed, not all of them (a match only has one league anyway).
    leagues: tuple[str, ...] = ()
    days_ahead: int = DEFAULT_DAYS_AHEAD
    # Whose captured prices to price legs from. None/blank = any bookmaker
    # this project has a real quote from -- see the module docstring for why
    # that is the default rather than an edge case.
    price_bookmaker: str | None = None

    def as_json(self) -> dict:
        """What gets stored on the ``BookingSlip`` row -- plain values only,
        so it round-trips through JSON without a custom decoder."""

        return {
            "bookmaker": self.bookmaker,
            "target_odds": self.target_odds,
            "markets": list(self.markets),
            "min_probability": self.min_probability,
            "max_legs": self.max_legs,
            "league": self.league,
            "leagues": list(self.leagues),
            "days_ahead": self.days_ahead,
            "price_bookmaker": self.price_bookmaker,
        }


@dataclass(frozen=True)
class Leg:
    match_id: int
    league: str
    home_team: str
    away_team: str
    kickoff: dt.datetime
    market: str
    selection: str
    model_probability: float
    # None on a leg resolved by resolve_legs_unpriced -- an Admin Pick an
    # operator chose to show as model-probability-only, no bookmaker quote
    # attached (AdminPick.priced=False). Every other producer of a Leg
    # (build_candidate_legs, price_legs) always sets a real price.
    decimal_odds: float | None = None
    # Which bookmaker's stored quote this price came from -- always a real
    # name, never "any" or blank, so a leg never hides where its number came
    # from behind the criteria's own (possibly unset) price_bookmaker. None
    # exactly when decimal_odds is None.
    priced_by: str | None = None

    def as_json(self) -> dict:
        return {
            "match_id": self.match_id,
            "league": self.league,
            "home_team": self.home_team,
            "away_team": self.away_team,
            "kickoff": self.kickoff.isoformat(),
            "market": self.market,
            "selection": self.selection,
            "model_probability": self.model_probability,
            "decimal_odds": self.decimal_odds,
            "priced_by": self.priced_by,
        }


@dataclass
class SelectionResult:
    criteria: SlipCriteria
    legs: list[Leg]
    combined_odds: float
    combined_probability: float
    candidates_considered: int
    met_target: bool
    warnings: list[str] = field(default_factory=list)

    @property
    def expires_at(self) -> dt.datetime | None:
        """A slip is dead the moment its earliest match kicks off -- there is
        nothing left to book once one leg has started."""

        return min((leg.kickoff for leg in self.legs), default=None)


def _latest_odds(
    db: Session, match_ids: list[int], price_bookmaker: str | None
) -> dict[tuple[int, str, str], tuple[float, str]]:
    """The most recent stored (price, bookmaker) per (match, market,
    selection). One query regardless of how many matches are in play -- the
    per-match version of this is the pattern that already cost this project
    a month of database bandwidth once.

    ``price_bookmaker`` set filters to that one bookmaker's own quotes, same
    as before this existed. Left unset, every bookmaker this project has
    captured a price from is pooled, and whichever quote was captured most
    recently for a given (match, market, selection) wins -- still always a
    real, attributable price, never a blend of several.
    """

    if not match_ids:
        return {}

    stmt = select(MatchOdds).where(MatchOdds.match_id.in_(match_ids)).order_by(MatchOdds.captured_at.asc())
    if price_bookmaker:
        stmt = stmt.where(MatchOdds.bookmaker == price_bookmaker)

    # Ascending order, so a later row for the same key overwrites an earlier
    # one -- the dict ends up holding only the latest snapshot per key,
    # whichever bookmaker it came from.
    latest: dict[tuple[int, str, str], tuple[float, str]] = {}
    for row in db.execute(stmt).scalars():
        latest[(row.match_id, row.market, row.selection)] = (row.decimal_odds, row.bookmaker)
    return latest


def _best_priced_outcome(
    outcomes: list[Outcome], wanted_markets: tuple[str, ...], priced: set[tuple[str, str]]
) -> Outcome | None:
    """The single strongest *priced* qualifying outcome for one match, so a
    match never contributes two correlated legs to the same slip.

    Priced first, then strongest -- not the other way round. A derived
    outcome like a Double Chance selection is a probability union and is
    therefore always at least as high as the 1X2 pick it's built from, so
    picking by probability alone before checking for a price means Double
    Chance always wins the comparison and then has no odds behind it,
    silently dropping a match that had a perfectly good priced leg.
    """

    candidates = [
        o for o in outcomes
        if (not wanted_markets or o.market in wanted_markets) and (o.market, o.selection) in priced
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda o: o.probability)


def build_candidate_legs(db: Session, criteria: SlipCriteria) -> list[Leg]:
    """Every match that could contribute a leg: has a prediction already
    (this never builds one on demand -- see ``routes_predictions.outcomes``
    for why generating a whole day's models because someone opened a page is
    the wrong trade), meets the accuracy floor, and has a real price --
    from ``criteria.price_bookmaker`` if set, otherwise from any bookmaker
    this project has captured one from.
    """

    now = dt.datetime.utcnow()
    cutoff = now + dt.timedelta(days=criteria.days_ahead)

    match_query = (
        select(Match)
        .where(Match.date >= now, Match.date < cutoff, Match.status == "SCHEDULED")
        .options(selectinload(Match.home_team), selectinload(Match.away_team))
    )
    if criteria.leagues:
        match_query = match_query.where(Match.league.in_(criteria.leagues))
    elif criteria.league:
        match_query = match_query.where(Match.league == criteria.league)
    matches = list(db.execute(match_query).scalars())
    if not matches:
        return []

    by_id = {m.id: m for m in matches}
    predictions = db.execute(
        select(Prediction)
        .where(Prediction.match_id.in_(list(by_id)))
        .order_by(Prediction.created_at.asc())
    ).scalars()
    latest_prediction: dict[int, Prediction] = {p.match_id: p for p in predictions}
    if not latest_prediction:
        return []

    prices = _latest_odds(db, list(latest_prediction), criteria.price_bookmaker)

    # Priced (market, selection) pairs, per match -- not pooled across every
    # match, or a pair priced for one fixture would look priced for all of
    # them and the lookup two lines down would raise on the mismatch.
    priced_by_match: dict[int, set[tuple[str, str]]] = {}
    for (match_id, market, selection) in prices:
        priced_by_match.setdefault(match_id, set()).add((market, selection))

    wanted_markets = criteria.markets or DEFAULT_MARKETS

    legs: list[Leg] = []
    for match_id, prediction in latest_prediction.items():
        outcomes = [
            o for o in outcomes_from_prediction(prediction) if o.probability >= criteria.min_probability
        ]
        chosen = _best_priced_outcome(outcomes, wanted_markets, priced_by_match.get(match_id, set()))
        if chosen is None:
            continue

        price, priced_by = prices[(match_id, chosen.market, chosen.selection)]
        match = by_id[match_id]
        legs.append(
            Leg(
                match_id=match.id,
                league=match.league,
                home_team=match.home_team.name,
                away_team=match.away_team.name,
                kickoff=match.date,
                market=chosen.market,
                selection=chosen.selection,
                model_probability=chosen.probability,
                decimal_odds=price,
                priced_by=priced_by,
            )
        )

    return legs


def select_legs(db: Session, criteria: SlipCriteria) -> SelectionResult:
    """Greedily assemble legs toward ``criteria.target_odds``, safest first.

    Sorting by probability descending before adding anything means the first
    legs chosen are always the ones with the least individual risk; the
    search stops as soon as the target is met rather than continuing to pad
    the slip with weaker picks it does not need.
    """

    warnings: list[str] = []

    if criteria.target_odds <= 1.0:
        return SelectionResult(
            criteria=criteria, legs=[], combined_odds=1.0, combined_probability=1.0,
            candidates_considered=0, met_target=False,
            warnings=["Target odds must be greater than 1.0 -- a single leg's own price already clears that."],
        )

    candidates = build_candidate_legs(db, criteria)
    candidates.sort(key=lambda leg: -leg.model_probability)

    chosen: list[Leg] = []
    combined_odds = 1.0
    combined_probability = 1.0

    for leg in candidates:
        if len(chosen) >= criteria.max_legs:
            break
        if combined_odds >= criteria.target_odds:
            break
        chosen.append(leg)
        combined_odds *= leg.decimal_odds
        combined_probability *= leg.model_probability

    met_target = combined_odds >= criteria.target_odds

    if not candidates:
        price_source = f"from {criteria.price_bookmaker!r}" if criteria.price_bookmaker else "from any bookmaker"
        warnings.append(
            f"No scheduled match in the next {criteria.days_ahead} day(s) both meets "
            f"{criteria.min_probability:.0%} accuracy and has a stored price {price_source}. Widen "
            "the window, lower the accuracy floor, or capture odds for this league first -- Settings "
            "-> Data sources, or run the import workflow with odds: yes."
        )
    elif not met_target and len(chosen) >= criteria.max_legs:
        warnings.append(
            f"Reached the {criteria.max_legs}-leg cap at {combined_odds:.2f}, short of the "
            f"{criteria.target_odds:.2f} target. Raise the leg cap or lower the accuracy floor "
            "to allow riskier legs in."
        )
    elif not met_target:
        warnings.append(
            f"Used every qualifying match ({len(candidates)}) and reached {combined_odds:.2f}, "
            f"short of the {criteria.target_odds:.2f} target. There is nothing left this accuracy "
            "floor and available pricing allow -- lower the floor, widen the window, or capture "
            "more odds to go further."
        )

    if chosen:
        drop = 1.0 - combined_probability
        warnings.append(
            f"Combined probability is {combined_probability:.0%}, not each leg's own "
            f"{'/'.join(f'{leg.model_probability:.0%}' for leg in chosen)} -- stacking "
            f"{len(chosen)} legs multiplies the risk by about {drop:.0%}, it doesn't add "
            "the confidence."
        )

    return SelectionResult(
        criteria=criteria,
        legs=chosen,
        combined_odds=combined_odds,
        combined_probability=combined_probability,
        candidates_considered=len(candidates),
        met_target=met_target,
        warnings=warnings,
    )


def price_legs(
    db: Session,
    refs: list[tuple[int, str, str]],
    price_bookmaker: str | None = None,
) -> tuple[list[Leg], list[str]]:
    """Prices an explicit list of (match_id, market, selection) refs -- no
    search, no target, just "what does this cost right now". For a caller
    that already knows exactly which picks it wants (a chat answer's picks,
    a Markets-page shortlist) and only needs real numbers attached to them.

    A ref is skipped -- and explained in the returned warnings, never
    silently dropped -- when its match doesn't exist, has no stored
    prediction, the (market, selection) pair isn't one the current
    prediction actually offers, there's no stored price for it, or a second
    ref for an already-priced match arrives (one leg per match, same
    reasoning as build_candidate_legs: two outcomes on the same fixture are
    correlated, not independent, so only the first is kept).
    """

    if not refs:
        return [], []

    match_ids = list({match_id for match_id, _, _ in refs})
    matches = {
        m.id: m
        for m in db.execute(
            select(Match)
            .where(Match.id.in_(match_ids))
            .options(selectinload(Match.home_team), selectinload(Match.away_team))
        ).scalars()
    }
    predictions: dict[int, Prediction] = {}
    for p in db.execute(
        select(Prediction).where(Prediction.match_id.in_(match_ids)).order_by(Prediction.created_at.asc())
    ).scalars():
        predictions[p.match_id] = p  # ascending order -- the last write per match wins

    prices = _latest_odds(db, match_ids, price_bookmaker)

    legs: list[Leg] = []
    warnings: list[str] = []
    priced_matches: set[int] = set()

    for match_id, market, selection in refs:
        match = matches.get(match_id)
        if match is None:
            warnings.append(f"Match {match_id}: not found, skipped.")
            continue
        label = f"{match.home_team.name} vs {match.away_team.name}"

        if match_id in priced_matches:
            warnings.append(f"{label}: already have a leg from this match, skipped {market} -- {selection}.")
            continue

        prediction = predictions.get(match_id)
        if prediction is None:
            warnings.append(f"{label}: no stored prediction, skipped.")
            continue

        outcome = find_outcome(prediction, market, selection)
        if outcome is None:
            warnings.append(
                f"{label}: {market} -- {selection} isn't an outcome the current prediction offers, skipped."
            )
            continue

        priced = prices.get((match_id, market, selection))
        if priced is None:
            warnings.append(f"{label}: no stored bookmaker price for {market} -- {selection}, skipped.")
            continue
        price, priced_by = priced

        legs.append(
            Leg(
                match_id=match.id,
                league=match.league,
                home_team=match.home_team.name,
                away_team=match.away_team.name,
                kickoff=match.date,
                market=outcome.market,
                selection=outcome.selection,
                model_probability=outcome.probability,
                decimal_odds=price,
                priced_by=priced_by,
            )
        )
        priced_matches.add(match_id)

    return legs, warnings


def resolve_legs_unpriced(
    db: Session,
    refs: list[tuple[int, str, str]],
) -> tuple[list[Leg], list[str]]:
    """Resolves an explicit list of (match_id, market, selection) refs
    against each match's current Prediction only -- same grounding as
    ``price_legs`` (a real match, a real stored prediction, an outcome that
    prediction actually offers, one leg per match), just without requiring a
    ``MatchOdds`` row. For an Admin Pick an operator wants to show as a
    model-probability-only combo (``AdminPick.priced=False``) -- most
    outcomes browsed on the Markets page never get a bookmaker quote
    captured for them, and requiring one here would make most of what
    someone actually picks unfeaturable.
    """

    if not refs:
        return [], []

    match_ids = list({match_id for match_id, _, _ in refs})
    matches = {
        m.id: m
        for m in db.execute(
            select(Match)
            .where(Match.id.in_(match_ids))
            .options(selectinload(Match.home_team), selectinload(Match.away_team))
        ).scalars()
    }
    predictions: dict[int, Prediction] = {}
    for p in db.execute(
        select(Prediction).where(Prediction.match_id.in_(match_ids)).order_by(Prediction.created_at.asc())
    ).scalars():
        predictions[p.match_id] = p  # ascending order -- the last write per match wins

    legs: list[Leg] = []
    warnings: list[str] = []
    resolved_matches: set[int] = set()

    for match_id, market, selection in refs:
        match = matches.get(match_id)
        if match is None:
            warnings.append(f"Match {match_id}: not found, skipped.")
            continue
        label = f"{match.home_team.name} vs {match.away_team.name}"

        if match_id in resolved_matches:
            warnings.append(f"{label}: already have a leg from this match, skipped {market} -- {selection}.")
            continue

        prediction = predictions.get(match_id)
        if prediction is None:
            warnings.append(f"{label}: no stored prediction, skipped.")
            continue

        outcome = find_outcome(prediction, market, selection)
        if outcome is None:
            warnings.append(
                f"{label}: {market} -- {selection} isn't an outcome the current prediction offers, skipped."
            )
            continue

        legs.append(
            Leg(
                match_id=match.id,
                league=match.league,
                home_team=match.home_team.name,
                away_team=match.away_team.name,
                kickoff=match.date,
                market=outcome.market,
                selection=outcome.selection,
                model_probability=outcome.probability,
            )
        )
        resolved_matches.add(match_id)

    return legs, warnings


def select_ranged_leg_slip(
    db: Session,
    criteria: SlipCriteria,
    leg_count: int,
    target_min: float,
    target_max: float,
) -> SelectionResult:
    """Picks exactly ``leg_count`` legs (one per match) whose combined odds
    land inside ``[target_min, target_max]`` -- the fixed-size,
    ranged-target shape scripts/generate_weekly_picks.py needs for its
    three risk tiers, distinct from ``select_legs``' variable-length
    "stop once a single floor is reached" search.

    Candidates are sorted by decimal_odds ascending, then scanned as a
    sliding window of ``leg_count`` consecutive entries. Every odds value is
    > 1, so sliding the window up by one always drops the window's cheapest
    price and adds one at least as large as everything already in it --
    the window's product is therefore non-decreasing as it slides, and the
    first window landing inside the target range is both correct and the
    safest (lowest-odds) one that qualifies. No combinatorial search across
    subsets is needed.

    Never widens the range to force a result: if no window's product falls
    inside it, the closest window found is returned anyway (so the caller
    can report how close it got) but ``met_target`` is False -- the same
    "refuse rather than mislabel" rule ``select_legs`` applies to its own
    target, just checked against a range instead of a floor. A caller
    publishing these to users should skip writing the tier that week
    rather than store a slip outside the range it claims to be.
    """

    candidates = build_candidate_legs(db, criteria)
    candidates.sort(key=lambda leg: leg.decimal_odds)

    if len(candidates) < leg_count:
        return SelectionResult(
            criteria=criteria, legs=[], combined_odds=1.0, combined_probability=1.0,
            candidates_considered=len(candidates), met_target=False,
            warnings=[
                f"Only {len(candidates)} qualifying match(es) available -- need {leg_count} for a full slip."
            ],
        )

    best_window: list[Leg] | None = None
    best_odds = 1.0
    closest_window: list[Leg] = candidates[:leg_count]
    closest_odds = 1.0
    for leg in closest_window:
        closest_odds *= leg.decimal_odds
    closest_distance = min(abs(closest_odds - target_min), abs(closest_odds - target_max))

    for start in range(0, len(candidates) - leg_count + 1):
        window = candidates[start:start + leg_count]
        odds = 1.0
        for leg in window:
            odds *= leg.decimal_odds

        if target_min <= odds <= target_max:
            best_window, best_odds = window, odds
            break  # first (safest) qualifying window along the sorted order

        distance = min(abs(odds - target_min), abs(odds - target_max))
        if distance < closest_distance:
            closest_window, closest_odds, closest_distance = window, odds, distance

    chosen = best_window if best_window is not None else closest_window
    combined_odds = best_odds if best_window is not None else closest_odds
    combined_probability = 1.0
    for leg in chosen:
        combined_probability *= leg.model_probability

    met_target = best_window is not None
    warnings: list[str] = []
    if not met_target:
        warnings.append(
            f"No {leg_count}-leg combination this week lands between {target_min:.2f} and "
            f"{target_max:.2f} combined odds -- the closest achievable is {combined_odds:.2f}. "
            "Not published rather than shown outside its stated range."
        )

    return SelectionResult(
        criteria=criteria,
        legs=chosen,
        combined_odds=combined_odds,
        combined_probability=combined_probability,
        candidates_considered=len(candidates),
        met_target=met_target,
        warnings=warnings,
    )


# Independent of which risk preset (if any) produced a slip -- labels the
# *resulting* combined probability, the same "result, not input" reasoning
# BetCodes.tsx's own resultRiskLabel uses on the frontend, mirrored here so
# an Admin Pick's stored risk tier can never disagree with how the AI
# Generation page would describe the same combined probability.
def risk_tier(combined_probability: float) -> str:
    if combined_probability >= 0.5:
        return "low"
    if combined_probability >= 0.2:
        return "medium"
    return "high"
