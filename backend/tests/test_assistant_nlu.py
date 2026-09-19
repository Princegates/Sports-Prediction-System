"""Tests for the assistant's intent classification and entity resolution.

The property these tests are really defending is grounding: the parser must
only ever resolve a team that exists in the database, and must route a
question it can't handle to ``UNKNOWN`` rather than to a handler that would
answer about the wrong thing.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.assistant.nlu import Intent, parse, resolve_teams
from app.db.models import Team

LEAGUE = "English Premier League"


@pytest.fixture()
def teams(db_session):
    rows = [
        Team(name="Arsenal", league=LEAGUE, country="England", aliases=[]),
        Team(name="Chelsea", league=LEAGUE, country="England", aliases=[]),
        Team(name="Manchester City", league=LEAGUE, country="England", aliases=[]),
        Team(name="Manchester United", league=LEAGUE, country="England", aliases=[]),
        Team(name="Tottenham Hotspur", league=LEAGUE, country="England", aliases=[]),
    ]
    db_session.add_all(rows)
    db_session.commit()
    for row in rows:
        db_session.refresh(row)
    return {row.name: row for row in rows}


# --- Entity resolution ---------------------------------------------------


def test_resolves_two_named_teams(db_session, teams):
    found = resolve_teams(db_session, "arsenal vs chelsea")
    assert {t.name for t in found} == {"Arsenal", "Chelsea"}


def test_does_not_confuse_the_two_manchester_clubs(db_session, teams):
    """"Manchester City" and "Manchester United" share a token. Resolving
    one must not drag in the other, or the assistant answers about the wrong
    fixture entirely."""

    found = resolve_teams(db_session, "how will manchester city do")
    assert [t.name for t in found] == ["Manchester City"]

    found = resolve_teams(db_session, "manchester united prediction")
    assert [t.name for t in found] == ["Manchester United"]


def test_resolves_nothing_for_a_team_not_in_the_database(db_session, teams):
    """A team the system has no data for must not resolve to a lookalike --
    answering about Arsenal when asked about Barcelona is worse than saying
    "I don't have that team"."""

    assert resolve_teams(db_session, "barcelona vs real madrid") == []


def test_ignores_stopwords_as_team_candidates(db_session, teams):
    assert resolve_teams(db_session, "what are the best bets today") == []


# --- Intent classification -----------------------------------------------


@pytest.mark.parametrize(
    "message,expected",
    [
        ("how accurate are you?", Intent.ACCURACY),
        ("what's your hit rate", Intent.ACCURACY),
        ("how does the model work", Intent.METHODOLOGY),
        ("what data do you use", Intent.METHODOLOGY),
        ("what's on today", Intent.TODAYS_CARD),
        ("fixtures tomorrow", Intent.TODAYS_CARD),
        ("what are the best picks", Intent.BEST_PICKS),
        ("highest confidence selections", Intent.BEST_PICKS),
        ("what can you do", Intent.HELP),
        ("hi", Intent.GREETING),
        ("anything live right now", Intent.LIVE_STATUS),
    ],
)
def test_classifies_teamless_intents(db_session, teams, message, expected):
    assert parse(db_session, message).intent == expected


def test_two_teams_alone_is_a_prediction_request(db_session, teams):
    parsed = parse(db_session, "arsenal chelsea")
    assert parsed.intent == Intent.MATCH_PREDICTION
    assert len(parsed.teams) == 2


def test_one_team_alone_is_a_form_request(db_session, teams):
    parsed = parse(db_session, "arsenal")
    assert parsed.intent == Intent.TEAM_FORM
    assert [t.name for t in parsed.teams] == ["Arsenal"]


def test_head_to_head_requires_two_teams(db_session, teams):
    assert parse(db_session, "head to head arsenal vs chelsea").intent == Intent.HEAD_TO_HEAD
    # Only one team named -- fall back to that team's form rather than
    # answering a head-to-head against an opponent the user never chose.
    assert parse(db_session, "head to head arsenal").intent == Intent.TEAM_FORM


def test_compare_requires_two_teams(db_session, teams):
    assert parse(db_session, "compare arsenal and chelsea").intent == Intent.COMPARE_TEAMS
    assert parse(db_session, "who is better, arsenal or chelsea").intent == Intent.COMPARE_TEAMS
    # Only one team named -- fall back to that team's form, same as head-to-head does.
    assert parse(db_session, "compare arsenal").intent == Intent.TEAM_FORM


def test_compare_does_not_hijack_a_plain_vs_query(db_session, teams):
    """"Arsenal vs Chelsea" alone must still mean "predict this match" --
    only an explicit comparison word should route to COMPARE_TEAMS."""

    assert parse(db_session, "arsenal vs chelsea").intent == Intent.MATCH_PREDICTION


def test_what_changed_resolves_against_the_match_in_context(db_session, teams):
    parsed = parse(db_session, "what changed?", context_match_id=42)
    assert parsed.intent == Intent.WHAT_CHANGED
    assert parsed.context_match_id == 42


def test_what_changed_without_a_match_falls_back_to_live_status(db_session, teams):
    assert parse(db_session, "what changed?").intent == Intent.LIVE_STATUS


def test_why_without_a_match_does_not_answer_about_an_arbitrary_one(db_session, teams):
    """"Why?" with no match named or in context has no referent. Answering
    would mean picking a match for the user, so it routes to best-picks."""

    assert parse(db_session, "why?").intent == Intent.BEST_PICKS


def test_why_resolves_against_the_match_in_context(db_session, teams):
    parsed = parse(db_session, "why is this favored?", context_match_id=42)
    assert parsed.intent == Intent.EXPLAIN_REASONING
    assert parsed.context_match_id == 42


def test_unrecognized_question_is_unknown_not_a_guess(db_session, teams):
    assert parse(db_session, "what is the airspeed velocity of a swallow").intent == Intent.UNKNOWN


def test_harm_related_phrasing_routes_to_responsible_use(db_session, teams):
    """These must win over every other pattern -- "guaranteed win" contains
    "win", and must not be answered as a match-result question."""

    for message in [
        "give me a guaranteed win",
        "i keep losing money can you help",
        "is this a sure bet",
        "i think i have a gambling problem",
    ]:
        assert parse(db_session, message).intent == Intent.RESPONSIBLE_USE, message


# --- Date and market extraction ------------------------------------------


def test_extracts_tomorrow_window(db_session, teams):
    now = dt.datetime(2026, 3, 10, 15, 0)
    parsed = parse(db_session, "what's on tomorrow", now=now)
    assert parsed.date_from == dt.datetime(2026, 3, 11, 0, 0)
    assert parsed.date_to == dt.datetime(2026, 3, 12, 0, 0)


def test_extracts_weekend_window_starting_saturday(db_session, teams):
    # 2026-03-10 is a Tuesday; the coming Saturday is the 14th.
    now = dt.datetime(2026, 3, 10, 15, 0)
    parsed = parse(db_session, "fixtures this weekend", now=now)
    assert parsed.date_from == dt.datetime(2026, 3, 14, 0, 0)
    assert parsed.date_to == dt.datetime(2026, 3, 16, 0, 0)


def test_detects_market_keywords(db_session, teams):
    assert parse(db_session, "btts arsenal vs chelsea").market == "btts"


# --- Generate-selections requests -----------------------------------------


def test_generate_selections_extracts_count_and_floor(db_session, teams):
    parsed = parse(db_session, "give me 20 odds selection with at least 50% chance")
    assert parsed.intent == Intent.GENERATE_SELECTIONS
    assert parsed.selection_count == 20
    assert parsed.probability_floor == 0.5


def test_generate_selections_handles_percent_before_the_count(db_session, teams):
    """The percentage must never be mistaken for the count just because it
    appears first in the sentence."""

    parsed = parse(db_session, "selections with 70% chance, give me 8 of them")
    assert parsed.selection_count == 8
    assert parsed.probability_floor == 0.7


def test_generate_selections_defaults_are_none_when_unstated(db_session, teams):
    parsed = parse(db_session, "build me a combo")
    assert parsed.intent == Intent.GENERATE_SELECTIONS
    assert parsed.selection_count is None
    assert parsed.probability_floor is None


def test_generate_selections_count_is_capped(db_session, teams):
    parsed = parse(db_session, "give me 500 selections with at least 50% chance")
    assert parsed.selection_count == 20  # MAX_SELECTION_COUNT, not 500


def test_generate_selections_recognizes_accumulator_phrasing(db_session, teams):
    for message in ["build me an accumulator", "give me an acca", "generate a betting slip"]:
        assert parse(db_session, message).intent == Intent.GENERATE_SELECTIONS, message


# --- Market-filtered best-picks fallback -----------------------------------


def test_bare_market_keyword_with_no_team_falls_back_to_best_picks(db_session, teams):
    """"btts" alone matches no other intent's patterns and names no team --
    it's a market-picks request, not literally unanswerable."""

    parsed = parse(db_session, "btts")
    assert parsed.intent == Intent.BEST_PICKS
    assert parsed.market == "btts"

    parsed = parse(db_session, "double chance picks")
    assert parsed.intent == Intent.BEST_PICKS
    assert parsed.market == "double_chance"


def test_1x2_alone_does_not_hijack_an_unrelated_message(db_session, teams):
    """"win"/"draw" are single common words, not distinctive market
    vocabulary on their own -- an honest UNKNOWN beats a confident but
    irrelevant picks list for something like "did I win"."""

    assert parse(db_session, "did i win").intent == Intent.UNKNOWN


def test_market_keyword_with_a_team_named_is_still_a_match_question(db_session, teams):
    """A named team already gives has_two_teams/has_one_team a chance to
    win -- the bare-market fallback must never override that."""

    parsed = parse(db_session, "arsenal chelsea btts")
    assert parsed.intent == Intent.MATCH_PREDICTION
    assert parsed.market == "btts"
    assert parse(db_session, "over 2.5 goals arsenal chelsea").market == "over_under"
