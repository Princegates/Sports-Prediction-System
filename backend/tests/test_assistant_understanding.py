"""Plain-English tolerance: typos, slang and nicknames are repaired toward
words the assistant knows -- and only toward those. The property under test is
as much "what is left alone" as "what is fixed": a wrong silent rewrite would
answer about the wrong team."""

from __future__ import annotations

import pytest

from app.assistant.nlu import Intent, parse
from app.assistant.understanding import did_you_mean, understand
from app.db.models import Team

LEAGUE = "English Premier League"
NAMES = {"arsenal", "chelsea", "tottenham", "hotspur", "manchester", "united", "english", "premier", "league"}
VOCAB = {"today", "tonight", "tomorrow", "picks", "accurate", "against", "double", "chance", "fixtures"}


@pytest.fixture()
def teams(db_session):
    rows = [
        Team(name="Arsenal", league=LEAGUE, country="England", aliases=[]),
        Team(name="Chelsea", league=LEAGUE, country="England", aliases=[]),
        Team(name="Manchester United", league=LEAGUE, country="England", aliases=["Man Utd"]),
        Team(name="Manchester City", league=LEAGUE, country="England", aliases=[]),
        Team(name="Tottenham Hotspur", league=LEAGUE, country="England", aliases=[]),
        Team(name="Aston Villa", league=LEAGUE, country="England", aliases=[]),
    ]
    db_session.add_all(rows)
    db_session.commit()
    return {t.name: t for t in rows}


def fixed(text: str) -> str:
    return understand(text, VOCAB, NAMES).text


def test_misspelt_teams_and_words_are_corrected_and_reported():
    result = understand("arsnal vs chelse tonite", VOCAB, NAMES)
    assert result.text == "arsenal vs chelsea tonight"
    # Slang is just spelt out; only the guesses are reported.
    assert ("arsnal", "arsenal") in result.assumed and ("chelse", "chelsea") in result.assumed
    assert all(a != "tonite" for a, _ in result.assumed)
    assert "arsnal" in (result.note or "")


@pytest.mark.parametrize("raw, expected", [
    ("wats on tmrw", "whats on tomorrow"),
    ("o2.5 goals", "over 2.5 goals"),
    ("u1.5", "under 1.5"),
    ("over2.5", "over 2.5"),
    ("2nd haf", "2nd half"),
])
def test_chat_shorthand_is_spelt_out(raw, expected):
    assert fixed(raw) == expected


@pytest.mark.parametrize("text", [
    "tell me a joke", "what is the weather", "the quick brown fox", "i love pizza", "hello there my friend",
    "arsenal", "chelsea today", "picks", "xyzzy plugh",
])
def test_ordinary_words_are_not_rewritten_into_football_vocabulary(text):
    result = understand(text, VOCAB, NAMES)
    assert result.text == text and not result.assumed


def test_a_known_word_is_never_corrected_to_a_neighbour():
    """"chance" and "change" are both real; neither becomes the other."""

    assert fixed("what is the chance") == "what is the chance"


def test_a_nickname_becomes_the_clubs_name_without_doubling_a_full_name():
    assert fixed("spurs v man utd") == "tottenham hotspur v manchester united"
    assert fixed("tottenham hotspur tonight") == "tottenham hotspur tonight"
    assert fixed("aston villa") == "aston villa"


def test_a_typo_resolves_to_the_right_fixture_and_is_reported(db_session, teams):
    parsed = parse(db_session, "arsnal vs chelse tonite")
    assert {t.name for t in parsed.teams} == {"Arsenal", "Chelsea"}
    assert parsed.intent == Intent.MATCH_PREDICTION
    assert ("arsnal", "arsenal") in parsed.assumed


def test_a_team_is_found_by_its_stored_alias_and_by_a_nickname(db_session, teams):
    assert [t.name for t in parse(db_session, "man utd form").teams] == ["Manchester United"]
    assert [t.name for t in parse(db_session, "will spurs beat arsenal").teams][:2] == ["Tottenham Hotspur", "Arsenal"]
    assert [t.name for t in parse(db_session, "villa tonight").teams] == ["Aston Villa"]


def test_an_unknown_team_is_still_not_swapped_for_a_lookalike(db_session, teams):
    assert parse(db_session, "barcelona vs madrid").teams == []
    assert parse(db_session, "barcelona vs madrid").assumed == []


@pytest.mark.parametrize("text, intent", [
    ("will arsenal win", Intent.MATCH_PREDICTION),
    ("arsenal next game", Intent.MATCH_PREDICTION),
    ("chelsea tonight", Intent.MATCH_PREDICTION),
    ("is chelsea gonna score", Intent.MATCH_PREDICTION),
    ("arsenal tips", Intent.MATCH_PREDICTION),
    ("arsenal form", Intent.TEAM_FORM),
    ("how is chelsea doing", Intent.TEAM_FORM),
    ("how acurate are u", Intent.ACCURACY),
    ("can i trust you", Intent.ACCURACY),
    ("what should i bet on", Intent.BEST_PICKS),
])
def test_loose_phrasings_land_on_the_obvious_intent(db_session, teams, text, intent):
    assert parse(db_session, text).intent == intent


def test_did_you_mean_offers_close_questions_only():
    assert "How accurate is the model?" in did_you_mean("acuracy of the model")
    assert "Best corners picks" in did_you_mean("best corner tip")
    assert did_you_mean("tell me a joke") == []
    assert did_you_mean("") == []
