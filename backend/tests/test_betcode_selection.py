"""The selection engine: turning preferences into real-priced legs.

The failure modes worth guarding against are the ones that would look fine
on screen and be wrong underneath -- a leg with no real price, two legs on
the same match multiplied together as if independent, or a combined
probability that quietly ignores what stacking actually costs.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.betcode.selection import SlipCriteria, build_candidate_legs, price_legs, select_legs
from app.db.models import Match, MatchOdds, Prediction, Team

BASE = dt.datetime.utcnow() + dt.timedelta(hours=6)


def _prediction(match_id: int, *, home=0.75, draw=0.15, away=0.10, btts_yes=0.55, btts_no=0.45) -> Prediction:
    return Prediction(
        match_id=match_id,
        model_version="test",
        home_win=home,
        draw=draw,
        away_win=away,
        over_probabilities={"2.5": 0.60},
        btts_yes=btts_yes,
        btts_no=btts_no,
        correct_score_probabilities={"2-1": 0.11},
        most_likely_score="2-1",
        most_likely_score_probability=0.11,
        global_outcome_market="Match Result",
        global_outcome_selection="Home Win",
        global_outcome_probability=home,
        confidence="HIGH",
        data_quality_score=1.0,
        model_agreement_score=0.9,
        explanation={"positive": [], "negative": []},
        model_breakdown={},
    )


@pytest.fixture()
def three_matches(db_session):
    """Three scheduled fixtures, each a heavy home favourite, each with a
    stored 'Bet9ja' price for Home Win. One extra match has BTTS priced too,
    to test the one-leg-per-match rule."""

    made = {}
    for name in ["Arsenal", "Chelsea", "Spurs", "Everton", "Villa", "Wolves"]:
        team = Team(name=name, league="English Premier League", aliases=[])
        db_session.add(team)
        made[name] = team
    db_session.commit()
    for t in made.values():
        db_session.refresh(t)

    pairs = [("Arsenal", "Chelsea", 0.90), ("Spurs", "Everton", 0.85), ("Villa", "Wolves", 0.80)]
    matches = []
    for i, (home, away, prob) in enumerate(pairs):
        m = Match(
            league="English Premier League", season="2025-26", date=BASE + dt.timedelta(days=i + 1),
            home_team_id=made[home].id, away_team_id=made[away].id, status="SCHEDULED",
        )
        db_session.add(m)
        matches.append(m)
    db_session.commit()
    for m, (_, _, prob) in zip(matches, pairs):
        db_session.refresh(m)
        db_session.add(_prediction(m.id, home=prob, draw=(1 - prob) * 0.6, away=(1 - prob) * 0.4))
        db_session.add(MatchOdds(
            match_id=m.id, bookmaker="Bet9ja", market="Match Result", selection="Home Win", decimal_odds=1.30,
        ))
    # The first match also has a qualifying BTTS price -- higher probability
    # than its Home Win, so the "best outcome per match" rule should prefer it.
    db_session.add(MatchOdds(
        match_id=matches[0].id, bookmaker="Bet9ja", market="Both Teams To Score", selection="No", decimal_odds=1.80,
    ))
    db_session.commit()
    return matches


def test_a_leg_needs_a_real_price_from_somewhere_not_just_a_qualifying_probability(db_session):
    """A match that clears the accuracy floor but has no stored price from
    *any* bookmaker contributes nothing -- there is no synthetic fallback
    price. Genuinely unpriced, not merely filtered to a league with no
    matches (that's a different, already-covered case)."""

    home = Team(name="Villa", league="English Premier League", aliases=[])
    away = Team(name="Wolves", league="English Premier League", aliases=[])
    db_session.add_all([home, away])
    db_session.commit()
    match = Match(
        league="English Premier League", season="2025-26", date=BASE + dt.timedelta(days=1),
        home_team_id=home.id, away_team_id=away.id, status="SCHEDULED",
    )
    db_session.add(match)
    db_session.commit()
    db_session.refresh(match)
    db_session.add(_prediction(match.id, home=0.9, draw=0.06, away=0.04))
    db_session.commit()

    legs = build_candidate_legs(db_session, SlipCriteria(bookmaker="SportyBet", target_odds=2.0, min_probability=0.5))
    assert legs == []


def test_which_bookmaker_the_code_is_for_does_not_affect_pricing(db_session, three_matches):
    """The bug this fixed: `bookmaker` used to double as both "who the final
    code is for" and "whose prices to use", so asking for a code from a
    bookmaker this project has never captured odds from (SportyBet, MSport,
    Bet9ja are Ghanaian brands; the odds this project actually captures come
    from whoever the data source returns, e.g. Bet365) silently zeroed every
    preview. `bookmaker` must now be free to be anything -- pricing pools
    whatever bookmaker's quote is actually on file, per price_bookmaker."""

    for name in ["SportyBet", "MSport", "Bet9ja", "A Bookmaker Nobody Captured Odds From"]:
        legs = build_candidate_legs(
            db_session, SlipCriteria(bookmaker=name, target_odds=2.0, min_probability=0.5)
        )
        assert len(legs) == 3, f"bookmaker={name!r} should not change which legs are found"
        assert all(leg.priced_by == "Bet9ja" for leg in legs)  # the only bookmaker three_matches actually priced


def test_price_bookmaker_set_still_filters_strictly(db_session, three_matches):
    """The escape hatch: someone who *does* care which bookmaker's numbers
    price the slip can still ask for exactly one."""

    legs = build_candidate_legs(
        db_session,
        SlipCriteria(bookmaker="Bet9ja", target_odds=2.0, min_probability=0.5, price_bookmaker="SportyBet"),
    )
    assert legs == []

    legs = build_candidate_legs(
        db_session,
        SlipCriteria(bookmaker="Bet9ja", target_odds=2.0, min_probability=0.5, price_bookmaker="Bet9ja"),
    )
    assert len(legs) == 3


def test_one_leg_per_match_prefers_the_stronger_outcome(db_session, three_matches):
    """The first match has both a 90% Home Win and, after correcting for the
    BTTS-No price set up above, a higher-probability BTTS candidate -- only
    one of the two may be used."""

    criteria = SlipCriteria(bookmaker="Bet9ja", target_odds=1.1, min_probability=0.5, max_legs=1, days_ahead=2)
    legs = build_candidate_legs(db_session, criteria)
    on_first_match = [leg for leg in legs if leg.match_id == three_matches[0].id]
    assert len(on_first_match) == 1


def test_market_filter_is_respected(db_session, three_matches):
    # The fixture's only BTTS price is "No" at its default 0.45 probability,
    # so the floor has to sit below that for this filter to have anything to
    # find -- separate from the accuracy floor used elsewhere in this file.
    criteria = SlipCriteria(
        bookmaker="Bet9ja", target_odds=1.5, min_probability=0.4, markets=("Both Teams To Score",),
    )
    legs = build_candidate_legs(db_session, criteria)
    assert legs and all(leg.market == "Both Teams To Score" for leg in legs)


def test_greedy_selection_stops_once_the_target_is_met(db_session, three_matches):
    result = select_legs(db_session, SlipCriteria(bookmaker="Bet9ja", target_odds=1.5, min_probability=0.5))
    assert result.met_target
    # 1.30 alone already clears nothing, but two legs (1.30 * 1.30 = 1.69) do,
    # and the search must stop there rather than adding every candidate.
    assert len(result.legs) == 2
    assert result.combined_odds == pytest.approx(1.69, abs=0.001)


def test_legs_are_chosen_safest_first(db_session, three_matches):
    """Sorted by model probability descending before anything is added, so a
    two-leg slip always uses the two strongest picks available, never a
    weaker one that happened to be considered first."""

    result = select_legs(db_session, SlipCriteria(bookmaker="Bet9ja", target_odds=1.5, min_probability=0.5))
    probabilities = [leg.model_probability for leg in result.legs]
    assert probabilities == sorted(probabilities, reverse=True)
    assert probabilities[0] == pytest.approx(0.90, abs=0.01)


def test_combined_probability_is_the_product_not_an_average(db_session, three_matches):
    result = select_legs(
        db_session, SlipCriteria(bookmaker="Bet9ja", target_odds=1.6, min_probability=0.5, max_legs=2),
    )
    expected = 1.0
    for leg in result.legs:
        expected *= leg.model_probability
    assert result.combined_probability == pytest.approx(expected)
    # Never mistakeable for a single leg's own probability -- multiplying two
    # numbers under 1 always produces something smaller than either.
    assert result.combined_probability < min(leg.model_probability for leg in result.legs)


def test_warns_plainly_about_the_stacking_cost_when_legs_are_chosen(db_session, three_matches):
    result = select_legs(db_session, SlipCriteria(bookmaker="Bet9ja", target_odds=1.5, min_probability=0.5))
    assert any("multiplies the risk" in w for w in result.warnings)


def test_never_swaps_in_a_weaker_leg_just_to_reach_the_target(db_session, three_matches):
    """The leg cap can stop a slip short of its target -- that is reported,
    not silently patched over by lowering the accuracy floor mid-search."""

    result = select_legs(
        db_session, SlipCriteria(bookmaker="Bet9ja", target_odds=100.0, min_probability=0.5, max_legs=2),
    )
    assert not result.met_target
    assert len(result.legs) == 2
    assert any("leg cap" in w or "target" in w for w in result.warnings)


def test_empty_database_produces_a_clear_warning_not_a_crash(db_session):
    result = select_legs(db_session, SlipCriteria(bookmaker="Bet9ja", target_odds=2.0))
    assert result.legs == []
    assert not result.met_target
    assert result.warnings


def test_a_target_of_one_or_less_is_rejected_up_front(db_session, three_matches):
    result = select_legs(db_session, SlipCriteria(bookmaker="Bet9ja", target_odds=1.0))
    assert result.legs == []
    assert "greater than 1.0" in result.warnings[0]


def test_matches_missing_a_prediction_are_never_built_on_demand(db_session):
    """Selection reads what already exists -- see the module docstring for
    why building a day's models because someone opened this page would be
    the wrong trade."""

    home = Team(name="A", league="English Premier League", aliases=[])
    away = Team(name="B", league="English Premier League", aliases=[])
    db_session.add_all([home, away])
    db_session.commit()
    db_session.add(Match(
        league="English Premier League", season="2025-26", date=BASE, status="SCHEDULED",
        home_team_id=home.id, away_team_id=away.id,
    ))
    db_session.commit()

    legs = build_candidate_legs(db_session, SlipCriteria(bookmaker="Bet9ja", target_odds=2.0, min_probability=0.1))
    assert legs == []
    assert db_session.query(Prediction).count() == 0


def test_league_filter_excludes_other_leagues(db_session, three_matches):
    result = select_legs(
        db_session, SlipCriteria(bookmaker="Bet9ja", target_odds=1.2, min_probability=0.5, league="Spanish La Liga"),
    )
    assert result.legs == []


def _add_la_liga_match(db_session) -> Match:
    home = Team(name="Real Madrid", league="Spanish La Liga", aliases=[])
    away = Team(name="Barcelona", league="Spanish La Liga", aliases=[])
    db_session.add_all([home, away])
    db_session.commit()
    for t in (home, away):
        db_session.refresh(t)

    match = Match(
        league="Spanish La Liga", season="2025-26", date=BASE + dt.timedelta(days=1),
        home_team_id=home.id, away_team_id=away.id, status="SCHEDULED",
    )
    db_session.add(match)
    db_session.commit()
    db_session.refresh(match)
    db_session.add(_prediction(match.id, home=0.85, draw=0.10, away=0.05))
    db_session.add(MatchOdds(
        match_id=match.id, bookmaker="Bet9ja", market="Match Result", selection="Home Win", decimal_odds=1.20,
    ))
    db_session.commit()
    return match


def test_leagues_plural_includes_matches_from_any_of_the_listed_leagues(db_session, three_matches):
    """Multi-league search: a match qualifies by being in ANY of the leagues
    listed, not all of them -- three EPL matches plus one La Liga match, all
    should come back when both leagues are named."""

    la_liga_match = _add_la_liga_match(db_session)

    result = select_legs(
        db_session,
        SlipCriteria(
            bookmaker="Bet9ja", target_odds=1_000_000.0, min_probability=0.5, max_legs=10,
            leagues=("English Premier League", "Spanish La Liga"),
        ),
    )
    match_ids = {leg.match_id for leg in result.legs}
    assert match_ids == {m.id for m in three_matches} | {la_liga_match.id}


def test_leagues_plural_still_excludes_leagues_not_listed(db_session, three_matches):
    _add_la_liga_match(db_session)

    result = select_legs(
        db_session,
        SlipCriteria(bookmaker="Bet9ja", target_odds=1_000_000.0, min_probability=0.5, leagues=("Spanish La Liga",)),
    )
    assert all(leg.league == "Spanish La Liga" for leg in result.legs)
    assert len(result.legs) == 1


def test_leagues_plural_takes_precedence_over_singular_league(db_session, three_matches):
    """Both fields set at once shouldn't happen from any real caller, but if
    it does, the newer multi-select field wins -- it's the one an actual UI
    control drives."""

    _add_la_liga_match(db_session)

    result = select_legs(
        db_session,
        SlipCriteria(
            bookmaker="Bet9ja", target_odds=1_000_000.0, min_probability=0.5,
            league="Spanish La Liga", leagues=("English Premier League",),
        ),
    )
    assert all(leg.league == "English Premier League" for leg in result.legs)
    assert len(result.legs) == 3


def test_default_markets_skip_a_trivial_near_certain_goal_line(db_session):
    """A bookmaker prices a Total Goals line for nearly every half/quarter
    line up to 8.5+, and one that far out clears well above 99% for almost
    any match -- correctly priced, and worthless as a recommendation, since
    the odds on it are barely above a stake-only return. Left unfiltered,
    that line would win _best_priced_outcome's "highest probability" compare
    against every real market on offer, every single time. Confirms
    DEFAULT_MARKETS keeps that out of the default pool while still picking a
    market a bettor would recognise -- here, Match Result."""

    home = Team(name="Arsenal", league="English Premier League", aliases=[])
    away = Team(name="Chelsea", league="English Premier League", aliases=[])
    db_session.add_all([home, away])
    db_session.commit()
    db_session.refresh(home)
    db_session.refresh(away)

    match = Match(
        league="English Premier League", season="2025-26", date=BASE, status="SCHEDULED",
        home_team_id=home.id, away_team_id=away.id,
    )
    db_session.add(match)
    db_session.commit()
    db_session.refresh(match)

    prediction = _prediction(match.id, home=0.70, draw=0.18, away=0.12)
    prediction.over_probabilities = {"2.5": 0.60, "7.5": 0.009}  # Under 7.5 = 1 - 0.009 = 99.1%
    db_session.add(prediction)
    db_session.add(MatchOdds(
        match_id=match.id, bookmaker="Bet9ja", market="Match Result", selection="Home Win", decimal_odds=1.60,
    ))
    db_session.add(MatchOdds(
        match_id=match.id, bookmaker="Bet9ja", market="Total Goals 7.5", selection="Under 7.5", decimal_odds=1.01,
    ))
    db_session.commit()

    legs = build_candidate_legs(
        db_session, SlipCriteria(bookmaker="Bet9ja", target_odds=2.0, min_probability=0.5),
    )

    assert len(legs) == 1
    assert (legs[0].market, legs[0].selection) == ("Match Result", "Home Win")


def test_an_extreme_goal_line_is_still_reachable_if_asked_for_explicitly(db_session):
    """DEFAULT_MARKETS only changes the *default* -- an unusual market is
    still selectable by naming it in criteria.markets."""

    home = Team(name="Arsenal", league="English Premier League", aliases=[])
    away = Team(name="Chelsea", league="English Premier League", aliases=[])
    db_session.add_all([home, away])
    db_session.commit()
    db_session.refresh(home)
    db_session.refresh(away)

    match = Match(
        league="English Premier League", season="2025-26", date=BASE, status="SCHEDULED",
        home_team_id=home.id, away_team_id=away.id,
    )
    db_session.add(match)
    db_session.commit()
    db_session.refresh(match)

    prediction = _prediction(match.id, home=0.70, draw=0.18, away=0.12)
    prediction.over_probabilities = {"2.5": 0.60, "7.5": 0.009}  # Under 7.5 = 1 - 0.009 = 99.1%
    db_session.add(prediction)
    db_session.add(MatchOdds(
        match_id=match.id, bookmaker="Bet9ja", market="Total Goals 7.5", selection="Under 7.5", decimal_odds=1.01,
    ))
    db_session.commit()

    legs = build_candidate_legs(
        db_session,
        SlipCriteria(bookmaker="Bet9ja", target_odds=1.05, min_probability=0.5, markets=("Total Goals 7.5",)),
    )

    assert len(legs) == 1
    assert (legs[0].market, legs[0].selection) == ("Total Goals 7.5", "Under 7.5")


# --- price_legs: pricing an explicit, already-chosen list of picks --------


def test_price_legs_prices_an_explicit_pick_from_a_real_stored_quote(db_session, three_matches):
    legs, warnings = price_legs(db_session, [(three_matches[0].id, "Match Result", "Home Win")])
    assert len(legs) == 1
    assert legs[0].decimal_odds == pytest.approx(1.30, abs=0.001)
    assert legs[0].priced_by == "Bet9ja"
    assert warnings == []


def test_price_legs_prices_several_picks_across_different_matches(db_session, three_matches):
    refs = [(m.id, "Match Result", "Home Win") for m in three_matches]
    legs, warnings = price_legs(db_session, refs)
    assert len(legs) == 3
    assert warnings == []


def test_price_legs_skips_a_pick_with_no_stored_price_and_says_so(db_session, three_matches):
    """Both Teams To Score is only priced for the first match -- asking for
    it on the second must be skipped, not synthesized."""

    legs, warnings = price_legs(db_session, [(three_matches[1].id, "Both Teams To Score", "Yes")])
    assert legs == []
    assert len(warnings) == 1
    assert "no stored bookmaker price" in warnings[0].lower()


def test_price_legs_keeps_only_the_first_pick_from_a_repeated_match(db_session, three_matches):
    """One leg per match, same reasoning as the search engine: a second
    outcome on an already-priced fixture is correlated, not independent."""

    refs = [
        (three_matches[0].id, "Match Result", "Home Win"),
        (three_matches[0].id, "Both Teams To Score", "No"),
    ]
    legs, warnings = price_legs(db_session, refs)
    assert len(legs) == 1
    assert legs[0].market == "Match Result"
    assert len(warnings) == 1
    assert "already have a leg from this match" in warnings[0]


def test_price_legs_skips_an_outcome_the_prediction_does_not_offer(db_session, three_matches):
    legs, warnings = price_legs(db_session, [(three_matches[0].id, "Match Result", "Nonexistent Selection")])
    assert legs == []
    assert len(warnings) == 1
    assert "isn't an outcome" in warnings[0]


def test_price_legs_skips_an_unknown_match(db_session, three_matches):
    legs, warnings = price_legs(db_session, [(999_999, "Match Result", "Home Win")])
    assert legs == []
    assert "not found" in warnings[0]


def test_price_legs_with_no_refs_returns_nothing(db_session):
    assert price_legs(db_session, []) == ([], [])
