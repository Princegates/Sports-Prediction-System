"""Settles a stored pick's legs against real match results.

Shared by AdminPick (the Super Admin's curated picks, shown as a public
track record) and BookingSlip (every member's own generated codes) --
both store the same ``legs: [{match_id, market, selection, ...}]`` shape,
so the same per-leg grading and accumulator logic applies to each. See
app.outcomes.grading.grade_outcome for what a leg's win/loss actually
turns on.

Settlement is lazy and one-way: it runs whenever a pick is read (list/
detail endpoints call ``settle_*`` before serializing), and once a pick
has left "pending" it is never re-graded automatically -- a Super Admin's
manual correction (routes_admin.set_admin_pick_result) is the only way
to change a settled result, so a frozen history never silently shifts
under a viewer who already saw it.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.betcode.selection import Leg
from app.db.models import AdminPick, BookingSlip, FeaturedPick, Match
from app.outcomes.grading import grade_outcome

# A pick's overall result, derived from its legs:
#   pending    -- at least one leg's match hasn't finished yet
#   lost       -- at least one leg is confirmed lost (the whole combo fails)
#   unresolved -- every match finished, nothing lost, but at least one leg
#                 is on a market grade_outcome doesn't cover -- needs a
#                 Super Admin to resolve it by hand
#   won        -- every leg confirmed won
RESULT_PENDING = "pending"
RESULT_WON = "won"
RESULT_LOST = "lost"
RESULT_UNRESOLVED = "unresolved"


def _grade_legs(db: Session, legs: list[dict]) -> list[str] | None:
    """Per-leg results ("pending" | "won" | "lost" | "unresolved"), or
    ``None`` if every leg's match data couldn't even be looked up."""

    match_ids = {leg["match_id"] for leg in legs}
    matches = {m.id: m for m in db.execute(select(Match).where(Match.id.in_(match_ids))).scalars()}

    leg_results: list[str] = []
    for leg in legs:
        match = matches.get(leg["match_id"])
        if match is None or not match.is_finished:
            leg_results.append(RESULT_PENDING)
            continue
        verdict = grade_outcome(
            leg["market"],
            leg["selection"],
            home_score=match.home_score,
            away_score=match.away_score,
            ht_home_score=match.ht_home_score,
            ht_away_score=match.ht_away_score,
        )
        if verdict is True:
            leg_results.append(RESULT_WON)
        elif verdict is False:
            leg_results.append(RESULT_LOST)
        else:
            leg_results.append(RESULT_UNRESOLVED)
    return leg_results


def _overall_result(leg_results: list[str]) -> str:
    if any(r == RESULT_LOST for r in leg_results):
        return RESULT_LOST
    if any(r == RESULT_PENDING for r in leg_results):
        return RESULT_PENDING
    if any(r == RESULT_UNRESOLVED for r in leg_results):
        return RESULT_UNRESOLVED
    return RESULT_WON


def _settle(db: Session, pick: AdminPick | BookingSlip) -> bool:
    """Mutates ``pick.result``/``leg_results``/``settled_at`` in place if
    it just reached a terminal state. Returns whether anything changed --
    the caller is responsible for ``db.commit()``."""

    if pick.result != RESULT_PENDING:
        return False  # already settled (or marked unresolved) -- frozen for good

    leg_results = _grade_legs(db, pick.legs)
    overall = _overall_result(leg_results)
    if overall == RESULT_PENDING:
        return False  # still waiting on at least one match to finish

    pick.leg_results = leg_results
    pick.result = overall
    pick.settled_at = dt.datetime.utcnow()
    return True


def settle_admin_pick(db: Session, pick: AdminPick) -> bool:
    return _settle(db, pick)


def settle_booking_slip(db: Session, slip: BookingSlip) -> bool:
    return _settle(db, slip)


def settle_featured_pick(db: Session, pick: FeaturedPick) -> bool:
    """FeaturedPick is a single (match, market, selection), not a list of
    legs, so it skips the accumulator logic _settle/_grade_legs use for
    AdminPick/BookingSlip: no "any leg lost fails the whole combo" case,
    just one outcome graded once its one match finishes."""

    if pick.result != RESULT_PENDING:
        return False

    match = db.get(Match, pick.match_id)
    if match is None or not match.is_finished:
        return False

    verdict = grade_outcome(
        pick.market,
        pick.selection,
        home_score=match.home_score,
        away_score=match.away_score,
        ht_home_score=match.ht_home_score,
        ht_away_score=match.ht_away_score,
    )
    pick.result = RESULT_WON if verdict is True else RESULT_LOST if verdict is False else RESULT_UNRESOLVED
    pick.settled_at = dt.datetime.utcnow()
    return True


def frozen_admin_pick_legs(db: Session, pick: AdminPick) -> list[Leg]:
    """Rebuilds ``pick``'s legs for display once it's settled, without
    re-resolving anything live (which would fail -- the match has already
    finished). Team names/league/kickoff come fresh from ``Match`` (permanent
    once played); market/selection/probability/odds come from the snapshot
    ``create_admin_pick``/``update_admin_pick`` capture on every leg at pick
    time specifically for this. A pre-settlement-feature row with no
    snapshot falls back to 0.0/None rather than crashing -- cosmetic only,
    and only ever affects a pick created before this shipped.
    """

    match_ids = {leg["match_id"] for leg in pick.legs}
    matches = {
        m.id: m
        for m in db.execute(
            select(Match).where(Match.id.in_(match_ids)).options(selectinload(Match.home_team), selectinload(Match.away_team))
        ).scalars()
    }
    leg_results = pick.leg_results or [None] * len(pick.legs)

    legs: list[Leg] = []
    for leg, leg_result in zip(pick.legs, leg_results):
        match = matches.get(leg["match_id"])
        if match is None:
            continue
        legs.append(
            Leg(
                match_id=match.id,
                league=match.league,
                home_team=match.home_team.name,
                away_team=match.away_team.name,
                kickoff=match.date,
                market=leg["market"],
                selection=leg["selection"],
                model_probability=leg.get("probability_at_pick") or 0.0,
                # admin_pick_to_schema asserts a priced pick's every leg has
                # real odds -- 1.0 is a harmless placeholder for the rare
                # pre-settlement-feature row with no captured snapshot.
                decimal_odds=(leg.get("decimal_odds_at_pick") or 1.0) if pick.priced else None,
                priced_by=None,
                leg_result=leg_result,
            )
        )
    return legs


def frozen_booking_slip_legs(slip: BookingSlip) -> list[Leg]:
    """Same idea as ``frozen_admin_pick_legs`` but for BookingSlip, whose
    ``legs`` already store the full Leg shape (team names, kickoff,
    probability, odds) at generation time -- see Leg.as_json -- so no
    database lookup is needed, just reattaching each leg's settled result.
    """

    leg_results = slip.leg_results or [None] * len(slip.legs)
    return [
        Leg(
            match_id=leg["match_id"],
            league=leg["league"],
            home_team=leg["home_team"],
            away_team=leg["away_team"],
            kickoff=dt.datetime.fromisoformat(leg["kickoff"]),
            market=leg["market"],
            selection=leg["selection"],
            model_probability=leg["model_probability"],
            decimal_odds=leg.get("decimal_odds"),
            priced_by=leg.get("priced_by"),
            leg_result=leg_result,
        )
        for leg, leg_result in zip(slip.legs, leg_results)
    ]
