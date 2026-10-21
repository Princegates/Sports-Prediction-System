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
import hashlib
import math
import random
from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.timeutil import utcnow
from app.db.models import Match, MatchOdds, Prediction
from app.outcomes.registry import Outcome, find_outcome, outcomes_from_prediction

DEFAULT_MIN_PROBABILITY = 0.65
DEFAULT_MAX_LEGS = 8
DEFAULT_DAYS_AHEAD = 7

# What "any market" actually searches when criteria.markets is left empty.
#
# It is deliberately not literally every priced market -- a bookmaker prices
# an extreme Total Goals line (Under 7.5, Under 8.5) barely above a
# stake-only return, which would win _best_priced_outcome's "highest
# probability" comparison against every other market every time and be
# worthless as a recommendation. This specific set is a product choice
# (fixed 2026-10, "AI Picks" should only ever lean on these): the handicap
# lines are ±1 and ±2 -- the registry offers nothing wider (see its
# HANDICAP_LINES).
# An unusual market is still reachable -- just by asking for it explicitly
# via criteria.markets, the same as any other specific choice.
DEFAULT_MARKETS = (
    "Match Result",
    "Double Chance",
    "Draw No Bet",
    "Total Goals 1.5",
    "Handicap -1",
    "Handicap +1",
    "Handicap -2",
    "Handicap +2",
)

# A ranked slip uses each market for one leg only (see _per_market_cap), so
# "the safest legs" doesn't mean the same market eight times over --
# Handicap +3 in particular clears almost any floor on almost every match.
# Handicap lines count as one market for this: "+3 Home" and "-3 Away" are
# the same bet shape from either side.
MAX_LEGS_PER_MARKET = 1

# How far a leg's probability can be nudged, for ranking purposes only, by
# _user_jitter below. Two different members asking for the same criteria
# otherwise land on the literal same slip every time -- select_legs_by_
# confidence and select_legs_featuring_every_market are both safest-first
# and fully deterministic, so there is nothing to make the second member's
# result differ from the first's. Bounded well under any real gap between
# genuinely different-quality legs (qualifying legs are rarely within two
# points of each other purely by coincidence, and when several are, they
# were already close enough that ranking between them is closer to noise
# than signal) -- this only ever reorders legs that were already roughly
# tied, never trades a meaningfully safer leg for a riskier one to produce
# variety. Not (re)tuned against real history the way DECAY_HALF_LIFE_DAYS
# or SHRINKAGE_MATCHES were; it only needs to be small relative to typical
# probability gaps; there's no "correct" value to fit.
MAX_USER_JITTER = 0.02


def _user_jitter(user_salt: int | None, match_id: int, market: str, selection: str) -> float:
    """A small, deterministic-per-(user, leg) nudge to a leg's rank.

    Deterministic so the same member generating the same criteria twice in
    a row (without clicking Refresh) gets the same slip both times -- this
    is about *different members* diverging, not about a single member's
    result becoming unstable. ``user_salt`` is typically the member's own
    account id; ``None`` (an unauthenticated caller, or one that simply
    doesn't thread a user through) disables jitter entirely, preserving the
    exact legacy ranking.
    """

    if user_salt is None:
        return 0.0
    digest = hashlib.sha256(f"{user_salt}:{match_id}:{market}:{selection}".encode()).digest()
    unit = int.from_bytes(digest[:4], "big") / 0xFFFFFFFF  # [0.0, 1.0]
    return (unit * 2.0 - 1.0) * MAX_USER_JITTER


def _market_family(market: str) -> str:
    return "Handicap" if market.startswith("Handicap ") else market


def _per_market_cap(max_legs: int, markets: tuple[str, ...]) -> int:
    """MAX_LEGS_PER_MARKET -- one leg per market -- for the default market
    set, even if that means fewer legs than asked. Only someone who chose
    their own markets, too few to fill max_legs one each, gets them
    repeated: picking just Over 1.5 still gives a full slip of it."""

    if not markets:
        return MAX_LEGS_PER_MARKET
    families = {_market_family(m) for m in markets}
    return max(MAX_LEGS_PER_MARKET, math.ceil(max_legs / len(families)))


# The fixed league pool for "AI Picks" -- the member-facing AI Generation
# page and the weekly system accumulators (scripts/generate_weekly_picks.py,
# generate_high_risk_slips.py), never a change to SlipCriteria's own
# "leagues=() means every league" convention, which other callers (the chat
# assistant, an admin's hand-built pick, the diagnostic scripts) still rely
# on unchanged. The 8 biggest, best-covered leagues this project tracks --
# same order AppShell.tsx's own LEAGUES list already leads with, before it
# drops into continental competitions and the smaller leagues beyond them.
AI_PICKS_LEAGUES = (
    "English Premier League",
    "Spanish La Liga",
    "German Bundesliga",
    "Italian Serie A",
    "French Ligue 1",
    "Dutch Eredivisie",
    "Portuguese Primeira Liga",
    "Turkish Süper Lig",
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
    # "won" | "lost" | "unresolved" once the owning pick has settled (see
    # app.pick_settlement), None on every live/pending leg. Never persisted
    # by as_json() -- a leg is only ever built already-settled when a caller
    # (routes_admin/_resolve_admin_pick, routes_betcodes's "my codes") passes
    # it in explicitly for display; this is never the shape stored on disk.
    leg_result: str | None = None

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

    now = utcnow()
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
    silently dropped -- when its match doesn't exist, has already finished,
    has no stored prediction, the (market, selection) pair isn't one the
    current prediction actually offers, there's no stored price for it, or a
    second ref for an already-priced match arrives (one leg per match, same
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

        if match.status == "FINISHED":
            warnings.append(f"{label}: match has finished, skipped {market} -- {selection}.")
            continue

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
    ``price_legs`` (a real match that hasn't finished, a real stored
    prediction, an outcome that prediction actually offers, one leg per
    match), just without requiring a ``MatchOdds`` row. For an Admin Pick an
    operator wants to show as a
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

        if match.status == "FINISHED":
            warnings.append(f"{label}: match has finished, skipped {market} -- {selection}.")
            continue

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


def select_floor_leg_slip(
    db: Session,
    criteria: SlipCriteria,
    min_legs: int,
    max_legs: int,
    target_min_odds: float,
    *,
    exclude_match_ids: frozenset[int] = frozenset(),
) -> SelectionResult:
    """Picks between ``min_legs`` and ``max_legs`` legs (one per match) whose
    combined odds clear ``target_min_odds`` -- scripts/generate_high_risk_slips.py's
    "mega accumulator" shape, distinct from ``select_ranged_leg_slip``'s fixed
    leg count and closed ``[target_min, target_max]`` band.

    Same safest-first philosophy as the rest of this module: candidates are
    sorted by decimal_odds ascending and taken in that order, never swapped
    around to land closer to the target. The twist here is the leg count
    itself is a range, not a single number -- the first ``min_legs`` are
    always taken (a slip this large needs the volume regardless of whether
    the floor is already cleared), then more are added, still cheapest
    first, only if the floor isn't cleared yet, stopping the moment it is or
    at ``max_legs`` if it never is.

    ``exclude_match_ids`` lets a caller building several of these in one run
    (three weekly "jackpot" slips, say) keep them from being near-duplicates
    of each other -- this is a deterministic sort over the same candidate
    pool, so without excluding what an earlier slip already used, a second
    call would mostly just reproduce the first.
    """

    warnings: list[str] = []
    candidates = [leg for leg in build_candidate_legs(db, criteria) if leg.match_id not in exclude_match_ids]
    candidates.sort(key=lambda leg: leg.decimal_odds)

    if len(candidates) < min_legs:
        return SelectionResult(
            criteria=criteria, legs=[], combined_odds=1.0, combined_probability=1.0,
            candidates_considered=len(candidates), met_target=False,
            warnings=[
                f"Only {len(candidates)} qualifying match(es) available -- need at least {min_legs} for a slip this size."
            ],
        )

    chosen: list[Leg] = []
    combined_odds = 1.0
    for leg in candidates:
        if len(chosen) >= max_legs:
            break
        if len(chosen) >= min_legs and combined_odds >= target_min_odds:
            break
        chosen.append(leg)
        combined_odds *= leg.decimal_odds

    combined_probability = 1.0
    for leg in chosen:
        combined_probability *= leg.model_probability

    met_target = combined_odds >= target_min_odds and len(chosen) >= min_legs
    if not met_target:
        warnings.append(
            f"Taking every available leg up to the {max_legs}-leg cap only reaches {combined_odds:.2f}, "
            f"short of the {target_min_odds:.2f} target. Not published rather than shown under its "
            "stated odds."
        )
    if chosen:
        drop = 1.0 - combined_probability
        warnings.append(
            f"Combined probability is {combined_probability:.1%} across {len(chosen)} legs -- "
            f"stacking this many multiplies the risk by about {drop:.0%}."
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


@dataclass
class ConfidencePick:
    """select_legs_by_confidence's result -- SelectionResult's shape without
    anything odds-related (combined_odds, met_target, target_odds), since
    nothing here is priced or combined toward a target. Booked directly with
    a betting site (app.betcode.sites) instead."""

    legs: list[Leg]
    combined_probability: float
    candidates_considered: int
    warnings: list[str] = field(default_factory=list)


def _match_window(
    days_ahead: int, date_from: dt.date | None, date_to: dt.date | None,
) -> tuple[dt.datetime, dt.datetime]:
    """The [start, end] window candidate matches are drawn from -- either an
    explicit date (or date range, both inclusive) from a calendar pick, or
    the usual rolling "next N days from now" default. A lone date_from with
    no date_to is a single day, not an open-ended "from here on": that
    matches the frontend calendar's one-click day pick, not a range picker.
    Never includes a match that's already kicked off today, even when
    date_from is today -- max() against `now` keeps that true for the
    date-based path the same way `Match.date >= now` already did for the
    days_ahead one.
    """

    now = utcnow()
    if date_from or date_to:
        start = max(now, dt.datetime.combine(date_from or date_to, dt.time.min))
        end = dt.datetime.combine(date_to or date_from, dt.time.max)
        return start, end
    return now, now + dt.timedelta(days=days_ahead)


def _window_description(days_ahead: int, date_from: dt.date | None, date_to: dt.date | None) -> str:
    """How a "no matches qualify" warning should name the window that came
    up empty -- a calendar pick reads oddly described back as a day count."""

    if date_from and date_to and date_from != date_to:
        return f"between {date_from.isoformat()} and {date_to.isoformat()}"
    if date_from or date_to:
        return f"on {(date_from or date_to).isoformat()}"
    return f"in the next {days_ahead} day(s)"


def build_confidence_candidates(
    db: Session,
    *,
    min_probability: float,
    markets: tuple[str, ...] = (),
    leagues: tuple[str, ...] = (),
    league: str | None = None,
    days_ahead: int = DEFAULT_DAYS_AHEAD,
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
    exclude_match_ids: frozenset[int] = frozenset(),
) -> list[Leg]:
    """Every match whose current prediction clears min_probability in a
    wanted market -- same matching as build_candidate_legs (one leg per
    match, the strongest qualifying outcome), minus the MatchOdds/pricing
    requirement. For a caller that books straight to a betting site
    (app.betcode.sites) rather than combining a priced accumulator, where a
    stored bookmaker quote would only drop otherwise-good matches for no
    reason -- see resolve_legs_unpriced's docstring for the same trade-off
    on an explicit pick.

    ``date_from``/``date_to`` (see _match_window) take over from
    ``days_ahead`` when given -- a calendar pick is more specific than a
    rolling window, not an addition to it.

    ``exclude_match_ids`` drops matches a caller already has legs from --
    AI Generation's own Refresh button sends back whatever the previous
    search returned, so running the exact same criteria again surfaces a
    genuinely different combination (the next-safest matches the earlier
    run didn't already use) instead of the identical, deterministic result
    safest-first search always lands on for unchanged criteria over
    unchanged data. Same idea select_floor_leg_slip already uses to keep
    several slips built in one run from duplicating each other.
    """

    start, end = _match_window(days_ahead, date_from, date_to)

    match_query = (
        select(Match)
        .where(Match.date >= start, Match.date <= end, Match.status == "SCHEDULED")
        .options(selectinload(Match.home_team), selectinload(Match.away_team))
    )
    if leagues:
        match_query = match_query.where(Match.league.in_(leagues))
    elif league:
        match_query = match_query.where(Match.league == league)
    if exclude_match_ids:
        match_query = match_query.where(Match.id.notin_(exclude_match_ids))
    matches = list(db.execute(match_query).scalars())
    if not matches:
        return []

    by_id = {m.id: m for m in matches}
    predictions = db.execute(
        select(Prediction).where(Prediction.match_id.in_(list(by_id))).order_by(Prediction.created_at.asc())
    ).scalars()
    latest_prediction: dict[int, Prediction] = {p.match_id: p for p in predictions}

    wanted_markets = markets or DEFAULT_MARKETS

    legs: list[Leg] = []
    for match_id, prediction in latest_prediction.items():
        outcomes = [
            o for o in outcomes_from_prediction(prediction)
            if o.probability >= min_probability and (not wanted_markets or o.market in wanted_markets)
        ]
        if not outcomes:
            continue
        chosen = max(outcomes, key=lambda o: o.probability)
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
            )
        )

    return legs


def select_legs_by_confidence(
    db: Session,
    *,
    min_probability: float = DEFAULT_MIN_PROBABILITY,
    max_legs: int = DEFAULT_MAX_LEGS,
    markets: tuple[str, ...] = (),
    leagues: tuple[str, ...] = (),
    league: str | None = None,
    days_ahead: int = DEFAULT_DAYS_AHEAD,
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
    exclude_match_ids: frozenset[int] = frozenset(),
    user_salt: int | None = None,
) -> ConfidencePick:
    """The no-odds sibling of select_legs: picks up to max_legs matches,
    safest (highest model probability) first, each clearing min_probability
    in a wanted market, with each market used for one leg only (see
    _per_market_cap) so a slip mixes markets. No price is required and there is no target to
    reach -- the result is meant to be booked directly with a betting site
    (app.betcode.sites), which prices the slip itself, rather than combined
    toward a target price the way select_legs's accumulator is.

    See build_confidence_candidates for what ``exclude_match_ids`` is for.
    ``user_salt`` is passed straight to _user_jitter -- see there for why.
    """

    # Every qualifying market per match (its strongest selection in each),
    # not just the match's single strongest outcome -- otherwise a capped
    # market leaves that match with nothing else to offer.
    pool = _build_random_pool(
        db, min_probability=min_probability, markets=markets, leagues=leagues, league=league, days_ahead=days_ahead,
        date_from=date_from, date_to=date_to, exclude_match_ids=exclude_match_ids,
    )
    candidates = [max(legs, key=lambda leg: leg.model_probability) for legs in pool.values()]
    cap = _per_market_cap(max_legs, markets)

    # Safest first, one leg per match, at most `cap` legs per market. The
    # jitter term only ever reorders legs within MAX_USER_JITTER of each
    # other -- see _user_jitter -- so this stays "safest first" in
    # substance while letting two members land on different legs among
    # ones that were already close to tied.
    options = sorted(
        (leg for legs in pool.values() for leg in legs),
        key=lambda leg: (
            -(leg.model_probability + _user_jitter(user_salt, leg.match_id, leg.market, leg.selection)),
            leg.kickoff, leg.match_id, leg.market,
        ),
    )
    chosen: list[Leg] = []
    used_matches: set[int] = set()
    per_market: Counter[str] = Counter()
    for leg in options:
        if len(chosen) >= max_legs:
            break
        family = _market_family(leg.market)
        if leg.match_id in used_matches or per_market[family] >= cap:
            continue
        chosen.append(leg)
        used_matches.add(leg.match_id)
        per_market[family] += 1

    combined_probability = 1.0
    for leg in chosen:
        combined_probability *= leg.model_probability

    warnings: list[str] = []
    if not candidates:
        warnings.append(
            f"No scheduled match {_window_description(days_ahead, date_from, date_to)} clears "
            f"{min_probability:.0%} model probability in these markets. Widen the window, lower the "
            "accuracy floor, or pick a different market."
        )
    elif len(candidates) > len(chosen):
        if len(chosen) < max_legs:
            warnings.append(
                f"{len(candidates)} matches qualify, but only {len(chosen)} legs fit while keeping each market "
                f"to {cap} leg{'s' if cap != 1 else ''} -- choose more markets or lower the accuracy floor for more."
            )
        else:
            warnings.append(
                f"{len(candidates)} matches qualify -- showing the {len(chosen)} safest by model probability, "
                f"with no more than {cap} legs from any one market."
            )
    if chosen:
        drop = 1.0 - combined_probability
        warnings.append(
            f"Combined probability is {combined_probability:.0%} -- stacking {len(chosen)} legs "
            f"multiplies the risk by about {drop:.0%}, it doesn't add the confidence."
        )

    return ConfidencePick(
        legs=chosen,
        combined_probability=combined_probability,
        candidates_considered=len(candidates),
        warnings=warnings,
    )


def select_legs_featuring_every_market(
    db: Session,
    *,
    min_probability: float = DEFAULT_MIN_PROBABILITY,
    max_legs: int = DEFAULT_MAX_LEGS,
    markets: tuple[str, ...] = (),
    leagues: tuple[str, ...] = (),
    league: str | None = None,
    days_ahead: int = DEFAULT_DAYS_AHEAD,
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
    exclude_match_ids: frozenset[int] = frozenset(),
    user_salt: int | None = None,
) -> ConfidencePick:
    """select_legs_by_confidence collapses each match to its single
    strongest qualifying outcome, regardless of which wanted market that
    is -- a match where Match Result clears 85% never contributes its own
    60% BTTS leg instead, even when BTTS was one of the markets explicitly
    asked for. This is the opposite trade: every wanted market gets its own
    leg, each on its own match, whenever the data allows -- for someone who
    ticked several markets specifically because they want all of them
    represented on the slip, not just whichever one keeps winning each
    match's own probability contest.

    Empty ``markets`` has nothing to feature, so this is identical to
    select_legs_by_confidence in that case.

    Two phases, both safest-first:

    **Guarantee.** Each wanted market's own strongest available leg is
    taken, in order of that market's own best probability (highest first)
    -- so when two wanted markets' strongest legs land on the same match,
    the stronger market keeps it and the weaker one falls back to its
    next-best match rather than losing out by coincidence of selection
    order. A market left with no qualifying, not-yet-used match is skipped
    and named in a warning, never forced in below the accuracy floor or
    onto a match another market already took. More wanted markets than
    ``max_legs`` allows means only that many -- the ones with the strongest
    available leg -- are guaranteed a slot; the rest are named in a warning.

    **Fill.** A handful of markets ticked with a much larger leg cap would
    otherwise hand back a slip far shorter than what was asked for -- two
    markets guaranteed one leg each is a 2-leg slip even when max_legs says
    8. Once every market that can be has a leg, this round-robins through
    the wanted markets (strongest first, same order as the guarantee phase)
    taking each one's next-best not-yet-used-match leg, cycling back
    through the list, until max_legs legs are chosen or every market has
    run out of qualifying, not-yet-used matches. A market can end up with
    several legs this way; no match or (match, market) pair is ever reused.
    """

    if not markets:
        return select_legs_by_confidence(
            db, min_probability=min_probability, max_legs=max_legs, markets=markets,
            leagues=leagues, league=league, days_ahead=days_ahead, date_from=date_from, date_to=date_to,
            exclude_match_ids=exclude_match_ids, user_salt=user_salt,
        )

    pool = _build_random_pool(
        db, min_probability=min_probability, markets=markets, leagues=leagues, league=league,
        days_ahead=days_ahead, date_from=date_from, date_to=date_to, exclude_match_ids=exclude_match_ids,
    )

    legs_by_market: dict[str, list[Leg]] = {}
    for match_legs in pool.values():
        for leg in match_legs:
            legs_by_market.setdefault(leg.market, []).append(leg)
    # Jittered the same way select_legs_by_confidence's own ranking is --
    # see _user_jitter -- so two members featuring the same markets don't
    # both land on the identical match for each one.
    for legs in legs_by_market.values():
        legs.sort(key=lambda l: -(l.model_probability + _user_jitter(user_salt, l.match_id, l.market, l.selection)))

    ranked = sorted(
        (m for m in markets if m in legs_by_market),
        key=lambda m: -legs_by_market[m][0].model_probability,
    )
    no_data = [m for m in markets if m not in legs_by_market]
    dropped_for_cap = ranked[max_legs:]
    guaranteed = ranked[:max_legs]

    chosen: list[Leg] = []
    used_matches: set[int] = set()
    # Index into each market's own sorted leg list -- a market's cursor
    # only ever moves forward, across both phases, so the fill phase picks
    # up exactly where the guarantee phase's own search left off instead of
    # re-scanning legs it already knows are either taken or already used.
    cursors: dict[str, int] = {m: 0 for m in guaranteed}
    no_available_match: list[str] = []

    def _next_available(market: str) -> Leg | None:
        legs = legs_by_market[market]
        i = cursors[market]
        while i < len(legs):
            if legs[i].match_id not in used_matches:
                cursors[market] = i + 1
                return legs[i]
            i += 1
        cursors[market] = i
        return None

    for market in guaranteed:
        leg = _next_available(market)
        if leg is None:
            no_available_match.append(market)
            continue
        chosen.append(leg)
        used_matches.add(leg.match_id)

    exhausted = set(no_available_match)
    while len(chosen) < max_legs and len(exhausted) < len(guaranteed):
        for market in guaranteed:
            if len(chosen) >= max_legs:
                break
            if market in exhausted:
                continue
            leg = _next_available(market)
            if leg is None:
                exhausted.add(market)
                continue
            chosen.append(leg)
            used_matches.add(leg.match_id)

    chosen.sort(key=lambda leg: -leg.model_probability)

    combined_probability = 1.0
    for leg in chosen:
        combined_probability *= leg.model_probability

    warnings: list[str] = []
    if not pool:
        warnings.append(
            f"No scheduled match {_window_description(days_ahead, date_from, date_to)} clears "
            f"{min_probability:.0%} model probability in any of the selected markets."
        )
    skipped = no_data + no_available_match
    if skipped:
        warnings.append(
            f"Couldn't feature: {', '.join(skipped)} -- no qualifying match available for it "
            "without reusing a match another selected market already took."
        )
    if dropped_for_cap:
        warnings.append(
            f"{len(markets)} markets selected but the leg cap is {max_legs} -- "
            f"{', '.join(dropped_for_cap)} didn't make the cut."
        )
    if chosen:
        drop = 1.0 - combined_probability
        warnings.append(
            f"Combined probability is {combined_probability:.0%} -- stacking {len(chosen)} legs "
            f"multiplies the risk by about {drop:.0%}, it doesn't add the confidence."
        )

    return ConfidencePick(
        legs=chosen,
        combined_probability=combined_probability,
        candidates_considered=len(pool),
        warnings=warnings,
    )


def _build_random_pool(
    db: Session,
    *,
    min_probability: float,
    markets: tuple[str, ...],
    leagues: tuple[str, ...],
    league: str | None,
    days_ahead: int,
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
    exclude_match_ids: frozenset[int] = frozenset(),
) -> dict[int, list[Leg]]:
    """Like build_confidence_candidates, but keyed by match_id with every
    qualifying *market* kept, not collapsed to the single strongest one.

    build_confidence_candidates picks max(outcomes, key=probability) per
    match -- the right call for select_legs_by_confidence's ranked slip,
    where the strongest signal should win. But a wide handicap line
    (Handicap +3: the home side only has to avoid losing by 4+) clears
    min_probability on almost every match and is very often *the*
    highest-probability outcome available, so a random draw sampled from
    that already-collapsed pool kept landing on "Handicap +3: Home" leg
    after leg -- technically a random pick of *matches*, but not of
    *markets*, which is what made a 7-leg slip read as one market repeated
    seven times. Keeping every qualifying market per match here, and
    picking one at random per match in select_random_legs below, makes the
    market itself part of the draw.

    Only the strongest *selection* within each market survives, though --
    a market with many selections (Correct Score's top-N scorelines
    especially) doesn't add market variety by keeping all of them, only
    outcome volume. With no market filter and every match in a wide
    "all leagues" window contributing one row per (market, selection) pair
    instead of one per market, this pool was the same unbounded-until-the-
    end memory shape that cost two other endpoints an OOM crash already
    today (see routes_matches.py and routes_predictions.py's own fixes) --
    collapsing to one Leg per (match, market) bounds it to
    matches x distinct-markets-wanted, regardless of how many selections
    any one market happens to offer.
    """

    start, end = _match_window(days_ahead, date_from, date_to)

    match_query = (
        select(Match)
        .where(Match.date >= start, Match.date <= end, Match.status == "SCHEDULED")
        .options(selectinload(Match.home_team), selectinload(Match.away_team))
    )
    if leagues:
        match_query = match_query.where(Match.league.in_(leagues))
    elif league:
        match_query = match_query.where(Match.league == league)
    if exclude_match_ids:
        match_query = match_query.where(Match.id.notin_(exclude_match_ids))
    matches = list(db.execute(match_query).scalars())
    if not matches:
        return {}

    by_id = {m.id: m for m in matches}
    predictions = db.execute(
        select(Prediction).where(Prediction.match_id.in_(list(by_id))).order_by(Prediction.created_at.asc())
    ).scalars()
    latest_prediction: dict[int, Prediction] = {p.match_id: p for p in predictions}

    wanted_markets = markets or DEFAULT_MARKETS

    pool: dict[int, list[Leg]] = {}
    for match_id, prediction in latest_prediction.items():
        match = by_id[match_id]
        outcomes = [
            o for o in outcomes_from_prediction(prediction)
            if o.probability >= min_probability and (not wanted_markets or o.market in wanted_markets)
        ]
        if not outcomes:
            continue

        # One outcome per market -- its own strongest selection -- not
        # every selection that market offers; see this function's own
        # docstring for why.
        best_per_market: dict[str, Outcome] = {}
        for o in outcomes:
            current = best_per_market.get(o.market)
            if current is None or o.probability > current.probability:
                best_per_market[o.market] = o

        pool[match_id] = [
            Leg(
                match_id=match.id,
                league=match.league,
                home_team=match.home_team.name,
                away_team=match.away_team.name,
                kickoff=match.date,
                market=o.market,
                selection=o.selection,
                model_probability=o.probability,
            )
            for o in best_per_market.values()
        ]

    return pool


def select_random_legs(
    db: Session,
    *,
    min_probability: float,
    min_legs: int,
    max_legs: int,
    markets: tuple[str, ...] = (),
    leagues: tuple[str, ...] = (),
    league: str | None = None,
    days_ahead: int = DEFAULT_DAYS_AHEAD,
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
    exclude_match_ids: frozenset[int] = frozenset(),
    rng: random.Random | None = None,
) -> ConfidencePick:
    """select_legs_by_confidence's random sibling: the same qualifying
    matches (_build_random_pool -- one *match* per leg, a wanted market,
    min_probability or better), drawn rather than ranked, with the market
    itself drawn too -- not always whichever single market happens to be
    each match's strongest (see _build_random_pool's docstring for why
    that collapsed every random slip onto one repeated market). Where
    select_legs_by_confidence always returns the same safest-first slip for
    a given pool, this returns a different slip -- a different leg count
    each time too, somewhere between min_legs and max_legs -- every time
    it's called, for a "wildcard" slip that isn't trying to be the single
    best combination, just a fair, honestly-labelled one.

    ``rng`` is injectable so a caller (a test, or a script writing several
    independent slips in one run) can get a reproducible -- or simply
    distinct -- draw; a fresh ``random.Random()`` is used otherwise.
    """

    rng = rng or random.Random()
    pool = _build_random_pool(
        db, min_probability=min_probability, markets=markets, leagues=leagues, league=league, days_ahead=days_ahead,
        date_from=date_from, date_to=date_to, exclude_match_ids=exclude_match_ids,
    )

    if not pool:
        return ConfidencePick(
            legs=[],
            combined_probability=0.0,
            candidates_considered=0,
            warnings=[
                f"No scheduled match {_window_description(days_ahead, date_from, date_to)} clears "
                f"{min_probability:.0%} model probability in these markets."
            ],
        )

    match_ids = list(pool)
    target_size = rng.randint(min_legs, max_legs) if max_legs > min_legs else min_legs
    size = min(target_size, len(match_ids))
    chosen_match_ids = rng.sample(match_ids, size)
    # One market drawn per match, not the match's own strongest -- this is
    # the actual fix: the same match can supply a Handicap +3 on one draw
    # and a Double Chance or Match Result on the next.
    chosen = [rng.choice(pool[match_id]) for match_id in chosen_match_ids]

    combined_probability = 1.0
    for leg in chosen:
        combined_probability *= leg.model_probability

    warnings: list[str] = []
    if len(match_ids) < min_legs:
        warnings.append(
            f"Only {len(match_ids)} matches clear {min_probability:.0%} right now -- fewer than the usual "
            f"{min_legs}-{max_legs}."
        )
    if chosen:
        warnings.append(
            f"Combined probability is {combined_probability:.0%} -- a random draw of {len(chosen)} legs, "
            "not the safest possible combination, and not sorted by risk level."
        )

    return ConfidencePick(
        legs=chosen,
        combined_probability=combined_probability,
        candidates_considered=len(match_ids),
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
