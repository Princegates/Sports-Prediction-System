"""End-to-end tests for the chat assistant API.

The central assertion running through these is that answers are *grounded*:
every figure the assistant states must appear in the database row it came
from. A test that only checked "returns 200 with some text" would pass just
as happily against an assistant that made the numbers up.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest
from fastapi.testclient import TestClient

from app import app_settings
from app.api import rate_limit
from app.assistant import llm
from app.auth.passwords import hash_password
from app.auth.tokens import create_token
from app.config import get_settings
from app.db.models import ChatMessage, LivePrediction, Match, MatchOdds, ModelMetric, Prediction, Team, User
from app.main import app
from tests.conftest import grant_active_access

client = TestClient(app)

LEAGUE = "English Premier League"


@pytest.fixture()
def fixture_data(db_session):
    """Two teams, a played match, an upcoming match, and a stored prediction
    for the upcoming one -- the minimum shape the assistant needs to answer
    a real question."""

    home = Team(name="Arsenal", league=LEAGUE, country="England", aliases=[])
    away = Team(name="Chelsea", league=LEAGUE, country="England", aliases=[])
    db_session.add_all([home, away])
    db_session.commit()
    db_session.refresh(home)
    db_session.refresh(away)

    now = dt.datetime.utcnow()

    # The upcoming fixture has to be BOTH today (the "what's on today?"
    # handler queries today 00:00 -> tomorrow 00:00) AND in the future
    # (best-picks ranks from now forward). A naive now + 6h satisfies both
    # in the morning and neither after 18:00 UTC, which made this suite pass
    # all morning and fail all evening -- the worst kind of flake, and one
    # that would break scheduled CI specifically.
    upcoming_kickoff = now + dt.timedelta(minutes=2)

    played = Match(
        league=LEAGUE,
        season="2025-26",
        date=now - dt.timedelta(days=30),
        home_team_id=home.id,
        away_team_id=away.id,
        home_score=2,
        away_score=1,
        status="FINISHED",
    )
    upcoming = Match(
        league=LEAGUE,
        season="2025-26",
        date=upcoming_kickoff,
        home_team_id=home.id,
        away_team_id=away.id,
        status="SCHEDULED",
    )
    db_session.add_all([played, upcoming])
    db_session.commit()
    db_session.refresh(upcoming)

    prediction = Prediction(
        match_id=upcoming.id,
        model_version="ensemble-v1",
        home_win=0.52,
        draw=0.26,
        away_win=0.22,
        over_probabilities={"0.5": 0.95, "1.5": 0.80, "2.5": 0.57, "3.5": 0.33, "4.5": 0.16},
        btts_yes=0.61,
        btts_no=0.39,
        correct_score_probabilities={"2-1": 0.11, "1-1": 0.10, "1-0": 0.09},
        most_likely_score="2-1",
        most_likely_score_probability=0.11,
        global_outcome_market="Total Goals 0.5",
        global_outcome_selection="Over 0.5",
        global_outcome_probability=0.95,
        confidence="HIGH",
        data_quality_score=0.9,
        model_agreement_score=0.88,
        explanation={
            "positive": ["Arsenal rated stronger than Chelsea (Elo edge: +75)", "Home advantage (+60 Elo-equivalent)"],
            "negative": ["Chelsea scoring 1.90 goals/game recently"],
        },
        model_breakdown={
            "elo": {"home_win": 0.55, "draw": 0.25, "away_win": 0.20, "elo_diff": 135.0},
            "poisson": {"home_win": 0.50, "draw": 0.27, "away_win": 0.23, "lambda_home": 1.7, "lambda_away": 1.2},
            "ml": {"H": 0.51, "D": 0.26, "A": 0.23},
        },
    )
    db_session.add(prediction)
    db_session.commit()
    db_session.refresh(prediction)

    return {"home": home, "away": away, "played": played, "upcoming": upcoming, "prediction": prediction}


def _ask(message: str, headers: dict, context_match_id: int | None = None):
    payload = {"message": message}
    if context_match_id is not None:
        payload["context_match_id"] = context_match_id
    response = client.post("/api/chat/message", json=payload, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


# --- Access control -------------------------------------------------------


def test_chat_requires_authentication(db_session):
    assert client.post("/api/chat/message", json={"message": "hello"}).status_code == 401
    assert client.get("/api/chat/history").status_code == 401


def test_user_without_access_cannot_chat(db_session, headers_no_access):
    """The assistant answers from prediction data, so it's gated the same as
    predictions themselves -- a logged-in account with no live access grant
    still can't reach it."""

    response = client.post(
        "/api/chat/message", json={"message": "hello"}, headers=headers_no_access
    )
    assert response.status_code == 403


def test_chat_history_is_scoped_to_its_own_user(db_session, auth_headers):
    """One user's conversation must not be readable from another's session."""

    _ask("hello", auth_headers)

    other = User(
        email="other-user@example.com",
        name="Other",
        password_hash=hash_password("a-good-password"),
        status="active",
    )
    db_session.add(other)
    db_session.commit()
    db_session.refresh(other)
    grant_active_access(db_session, other)
    settings = get_settings()
    other_token = create_token(
        {"user_id": other.id, "role": "user"}, settings.secret_key, settings.session_ttl_seconds
    )

    response = client.get("/api/chat/history", headers={"Authorization": f"Bearer {other_token}"})
    assert response.status_code == 200
    assert response.json() == []


# --- Grounding ------------------------------------------------------------


def test_match_prediction_quotes_the_stored_numbers(db_session, auth_headers, fixture_data):
    body = _ask("arsenal vs chelsea", auth_headers)
    text = body["text"]

    p = fixture_data["prediction"]
    # Every probability in the answer must be the stored one.
    assert "52%" in text  # home_win
    assert "26%" in text  # draw
    assert "22%" in text  # away_win
    assert "61%" in text  # btts_yes
    assert "2-1" in text  # most_likely_score
    assert p.global_outcome_selection in text
    assert body["intent"] == "match_prediction"
    assert body["includes_probability"] is True
    assert body["caveat"]  # forward-looking answer carries the caveat


def test_answer_cites_the_match_it_used(db_session, auth_headers, fixture_data):
    body = _ask("arsenal vs chelsea", auth_headers)
    match_sources = [s for s in body["sources"] if s["kind"] == "match"]
    assert match_sources
    assert match_sources[0]["ref"] == fixture_data["upcoming"].id


def test_explain_uses_the_stored_explanation_not_a_template(db_session, auth_headers, fixture_data):
    body = _ask("why is this favored?", auth_headers, context_match_id=fixture_data["upcoming"].id)
    assert body["intent"] == "explain_reasoning"
    assert "Elo edge: +75" in body["text"]
    assert "Home advantage" in body["text"]


def test_risks_surfaces_the_negative_factors(db_session, auth_headers, fixture_data):
    body = _ask("what are the risks?", auth_headers, context_match_id=fixture_data["upcoming"].id)
    assert body["intent"] == "risks"
    assert "Chelsea scoring 1.90 goals/game recently" in body["text"]


def test_correct_score_lists_stored_scorelines_only(db_session, auth_headers, fixture_data):
    body = _ask("what will the correct score be", auth_headers, context_match_id=fixture_data["upcoming"].id)
    assert body["intent"] == "correct_score"
    for score in ("2-1", "1-1", "1-0"):
        assert score in body["text"]


def test_accuracy_refuses_to_invent_a_number_without_a_backtest(db_session, auth_headers, fixture_data):
    """With no ModelMetric rows, the honest answer is "no backtest recorded"
    -- never a plausible-looking hit rate."""

    body = _ask("how accurate are you?", auth_headers)
    assert body["intent"] == "accuracy"
    assert "no backtest" in body["text"].lower()
    # Must not contain a fabricated percentage.
    assert "%" not in body["text"]


def test_accuracy_quotes_the_held_out_test_split(db_session, auth_headers, fixture_data):
    db_session.add_all(
        [
            # A flattering train-split number that must NOT be the headline.
            ModelMetric(model_version="ensemble-v1", split="train", league=LEAGUE, metric_name="accuracy", metric_value=0.81),
            ModelMetric(model_version="ensemble-v1", split="test", league=LEAGUE, metric_name="accuracy", metric_value=0.54),
            ModelMetric(model_version="ensemble-v1", split="test", league=LEAGUE, metric_name="log_loss", metric_value=0.9812),
        ]
    )
    db_session.commit()

    body = _ask("how accurate are you?", auth_headers)
    assert "54.0%" in body["text"]
    # The flattering train-split figure stays out of the headline entirely.
    assert "81.0%" not in body["text"]
    assert "0.9812" in body["text"]
    assert "test split" in body["text"]


def test_missing_prediction_offers_to_generate_rather_than_estimating(db_session, auth_headers, fixture_data):
    """A fixture with no stored prediction must not get an improvised one."""

    db_session.delete(fixture_data["prediction"])
    db_session.commit()

    body = _ask("arsenal vs chelsea", auth_headers)
    assert "no prediction has been generated" in body["text"]
    assert "%" not in body["text"]


def test_unknown_team_is_not_silently_swapped_for_a_known_one(db_session, auth_headers, fixture_data):
    """Asking about teams that aren't in the database must not get answered
    using the fixture that *is* there. The reply may name a known team as an
    example of a working query -- what it must not do is state a prediction,
    so the assertion is on probabilities and sources, not on the string."""

    body = _ask("barcelona vs real madrid", auth_headers)
    assert body["intent"] == "unknown"
    assert "%" not in body["text"]
    assert body["includes_probability"] is False
    assert body["sources"] == []


def test_head_to_head_counts_real_results(db_session, auth_headers, fixture_data):
    body = _ask("head to head arsenal vs chelsea", auth_headers)
    assert body["intent"] == "head_to_head"
    # One played match in the fixture: Arsenal 2-1 Chelsea.
    assert "Arsenal: 1 wins" in body["text"]
    assert "Chelsea: 0 wins" in body["text"]


def test_compare_teams_reports_each_sides_own_form(db_session, auth_headers, fixture_data):
    """Distinct from head-to-head: each team's own record, not their record
    against each other. Arsenal won the one played match in the fixture
    (2-1 as home), so it should lead on every metric here."""

    body = _ask("compare arsenal and chelsea", auth_headers)
    assert body["intent"] == "compare_teams"
    assert "Points per game: Arsenal 3.00 vs Chelsea 0.00 -- edge Arsenal" in body["text"]
    assert "Goals scored per game: Arsenal 2.00 vs Chelsea 1.00 -- edge Arsenal" in body["text"]


def test_what_changed_explains_a_live_probability_swing(db_session, auth_headers, fixture_data):
    match = fixture_data["upcoming"]
    db_session.add_all(
        [
            LivePrediction(
                match_id=match.id, minute=10, score_home=0, score_away=0,
                home_win=0.52, draw=0.26, away_win=0.22, over_probabilities={},
                btts_yes=0.5, global_outcome_market="Total Goals 0.5",
                global_outcome_selection="Over 0.5", global_outcome_probability=0.60,
                trigger_event="kickoff",
            ),
            LivePrediction(
                match_id=match.id, minute=23, score_home=1, score_away=0,
                home_win=0.75, draw=0.15, away_win=0.10, over_probabilities={},
                btts_yes=0.5, global_outcome_market="Total Goals 0.5",
                global_outcome_selection="Over 0.5", global_outcome_probability=0.80,
                trigger_event="goal",
            ),
        ]
    )
    db_session.commit()

    body = _ask("what changed?", auth_headers, context_match_id=match.id)
    assert body["intent"] == "what_changed"
    assert "moved up from 60% to 80%" in body["text"]
    assert "driven by: goal" in body["text"]
    assert "Score moved from 0-0 to 1-0" in body["text"]


def test_what_changed_with_no_live_events_says_so(db_session, auth_headers, fixture_data):
    body = _ask("what changed?", auth_headers, context_match_id=fixture_data["upcoming"].id)
    assert body["intent"] == "what_changed"
    assert "No live events have been recorded yet" in body["text"]


def test_todays_card_reports_actual_fixtures(db_session, auth_headers, fixture_data):
    now = dt.datetime.utcnow()
    if (now + dt.timedelta(minutes=2)).date() != now.date():
        pytest.skip("fixture kickoff crosses midnight UTC; today's-card window is untestable here")

    body = _ask("what's on today?", auth_headers)
    assert body["intent"] == "todays_card"
    assert "Arsenal" in body["text"] and "Chelsea" in body["text"]


def test_best_picks_ranks_by_stored_probability(db_session, auth_headers, fixture_data):
    body = _ask("what are the best picks?", auth_headers)
    assert body["intent"] == "best_picks"
    assert "Over 0.5" in body["text"]
    # The honest caveat that high probability != high value must be present.
    assert "value" in body["text"].lower()
    # Structured refs, so the client can offer "price these for real" via
    # AI Generation without re-parsing the prose.
    assert body["picks"] == [
        {"match_id": fixture_data["upcoming"].id, "market": "Total Goals 0.5", "selection": "Over 0.5"}
    ]


def test_generate_selections_uses_the_real_priced_selection_engine(db_session, auth_headers, fixture_data):
    """The chat answer must come from the same engine (and the same real,
    stored prices) AI Generation's own page uses -- never an estimated odds
    figure invented for the reply."""

    db_session.add(
        MatchOdds(
            match_id=fixture_data["upcoming"].id,
            bookmaker="Bet365",
            market="Match Result",
            selection="Home Win",
            decimal_odds=1.85,
        )
    )
    db_session.commit()

    body = _ask("give me 5 selections with at least 50% chance", auth_headers)
    assert body["intent"] == "generate_selections"
    assert "Home Win" in body["text"]
    assert "Arsenal vs Chelsea" in body["text"]
    assert "1.85" in body["text"]
    assert "Bet365" in body["text"]


def test_generate_selections_with_no_priced_match_says_so_honestly(db_session, auth_headers, fixture_data):
    """No MatchOdds seeded here -- there is nothing to price, and the
    assistant must say that rather than quote a made-up combo."""

    body = _ask("give me 5 selections with at least 50% chance", auth_headers)
    assert body["intent"] == "generate_selections"
    assert "nothing" in body["text"].lower()


def test_generate_selections_a_named_market_overrides_the_higher_probability_default(
    db_session, auth_headers, fixture_data
):
    """Regression test: a named market used to be parsed by the NLU and then
    silently dropped before it ever reached SlipCriteria, so it had zero
    effect on what got picked. With both Match Result (52%) and BTTS (61%)
    priced, the default (no market named) picks BTTS for being the higher
    probability -- asking for "match result" specifically must still return
    the Match Result leg, proving the request actually reaches the engine."""

    db_session.add_all(
        [
            MatchOdds(
                match_id=fixture_data["upcoming"].id, bookmaker="Bet365",
                market="Match Result", selection="Home Win", decimal_odds=1.85,
            ),
            MatchOdds(
                match_id=fixture_data["upcoming"].id, bookmaker="Bet365",
                market="Both Teams To Score", selection="Yes", decimal_odds=1.60,
            ),
        ]
    )
    db_session.commit()

    unfiltered = _ask("build me an accumulator with at least 50% chance", auth_headers)
    assert "Both Teams To Score" in unfiltered["text"]

    filtered = _ask("build me a match result accumulator with at least 50% chance", auth_headers)
    assert filtered["intent"] == "generate_selections"
    assert "Home Win" in filtered["text"]
    assert "Both Teams To Score" not in filtered["text"]


def test_generate_selections_a_named_league_filters_without_naming_a_team(db_session, auth_headers, fixture_data):
    """A league named by text alone ("la liga"), no team involved, must
    filter the search the same way a named team's league already did."""

    home = Team(name="Real Madrid", league="Spanish La Liga", country="Spain", aliases=[])
    away = Team(name="Barcelona", league="Spanish La Liga", country="Spain", aliases=[])
    db_session.add_all([home, away])
    db_session.commit()
    db_session.refresh(home)
    db_session.refresh(away)

    kickoff = dt.datetime.utcnow() + dt.timedelta(minutes=5)
    la_liga_match = Match(
        league="Spanish La Liga", season="2025-26", date=kickoff,
        home_team_id=home.id, away_team_id=away.id, status="SCHEDULED",
    )
    db_session.add(la_liga_match)
    db_session.commit()
    db_session.refresh(la_liga_match)

    la_liga_prediction = Prediction(
        match_id=la_liga_match.id, model_version="ensemble-v1",
        home_win=0.70, draw=0.18, away_win=0.12,
        over_probabilities={"2.5": 0.5}, btts_yes=0.4, btts_no=0.6,
        correct_score_probabilities={"2-0": 0.1},
        most_likely_score="2-0", most_likely_score_probability=0.1,
        global_outcome_market="Match Result", global_outcome_selection="Home Win",
        global_outcome_probability=0.70, confidence="HIGH",
        data_quality_score=0.9, model_agreement_score=0.88,
        explanation={"positive": [], "negative": []}, model_breakdown={},
    )
    db_session.add(la_liga_prediction)
    db_session.add_all(
        [
            MatchOdds(
                match_id=la_liga_match.id, bookmaker="Bet365",
                market="Match Result", selection="Home Win", decimal_odds=1.40,
            ),
            MatchOdds(
                match_id=fixture_data["upcoming"].id, bookmaker="Bet365",
                market="Match Result", selection="Home Win", decimal_odds=1.85,
            ),
        ]
    )
    db_session.commit()

    body = _ask("give me a la liga accumulator", auth_headers)
    assert body["intent"] == "generate_selections"
    assert "Real Madrid" in body["text"]
    assert "Arsenal" not in body["text"]


def test_generate_selections_high_risk_tier_supplies_its_own_defaults_and_a_caution(
    db_session, auth_headers, fixture_data
):
    db_session.add(
        MatchOdds(
            match_id=fixture_data["upcoming"].id, bookmaker="Bet365",
            market="Match Result", selection="Home Win", decimal_odds=1.85,
        )
    )
    db_session.commit()

    body = _ask("give me a high risk accumulator", auth_headers)
    assert body["intent"] == "generate_selections"
    assert "High risk slip" in body["text"]
    assert "50%" in body["text"]  # the tier's own floor, not the plain 65% default
    assert "Caution:" in body["text"]


def test_generate_selections_low_risk_tier_has_no_caution(db_session, auth_headers, fixture_data):
    prediction = fixture_data["prediction"]
    prediction.home_win = 0.90
    db_session.commit()
    db_session.add(
        MatchOdds(
            match_id=fixture_data["upcoming"].id, bookmaker="Bet365",
            market="Match Result", selection="Home Win", decimal_odds=1.20,
        )
    )
    db_session.commit()

    body = _ask("give me a low risk combo", auth_headers)
    assert body["intent"] == "generate_selections"
    assert "Low risk slip" in body["text"]
    assert "Caution:" not in body["text"]


def test_generate_selections_higher_risk_tier_mentions_the_ghana_helpline(db_session, auth_headers, fixture_data):
    db_session.add(
        MatchOdds(
            match_id=fixture_data["upcoming"].id, bookmaker="Bet365",
            market="Match Result", selection="Home Win", decimal_odds=1.85,
        )
    )
    db_session.commit()

    body = _ask("give me the maximum risk combo", auth_headers)
    assert body["intent"] == "generate_selections"
    assert "Higher risk slip" in body["text"]
    assert "0800 678 678" in body["text"]
    assert "Caution:" in body["text"]


def test_market_filtered_best_picks_ranks_by_that_markets_own_probability(db_session, auth_headers, fixture_data):
    """"btts" alone must rank by the BTTS outcome's own probability (61%),
    not by the match's unrelated global most-likely outcome (Over 0.5,
    95%) -- a plain best-picks answer would report the wrong number here."""

    body = _ask("btts", auth_headers)
    assert body["intent"] == "best_picks"
    assert "Both Teams To Score" in body["text"]
    assert "Yes" in body["text"]
    assert "61.0%" in body["text"]
    assert body["picks"] == [
        {"match_id": fixture_data["upcoming"].id, "market": "Both Teams To Score", "selection": "Yes"}
    ]


def test_picks_survive_a_reload_via_history(db_session, auth_headers, fixture_data):
    _ask("what are the best picks?", auth_headers)
    history = client.get("/api/chat/history", headers=auth_headers).json()
    assistant_row = next(r for r in history if r["role"] == "assistant")
    assert assistant_row["picks"] == [
        {"match_id": fixture_data["upcoming"].id, "market": "Total Goals 0.5", "selection": "Over 0.5"}
    ]


def test_answers_are_not_marked_rewritten_when_the_llm_rewriter_is_off(db_session, auth_headers, fixture_data):
    body = _ask("what are the best picks?", auth_headers)
    assert body["rewritten"] is False


def test_picks_and_the_rewritten_flag_survive_an_llm_rewrite(db_session, auth_headers, fixture_data, monkeypatch):
    """Regression test: engine.compose() used to rebuild the Answer dataclass
    field by field after a rewrite, which silently dropped `picks` the moment
    the rewriter was turned on. dataclasses.replace() fixed that -- this pins
    it down end-to-end, through the real API, the way a user would hit it."""

    app_settings.set_values(
        db_session,
        {
            "assistant_llm_enabled": True,
            "assistant_llm_base_url": "https://example-llm.test/v1",
            "assistant_llm_model": "test-model",
        },
    )

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "Here's the top pick, rephrased."}}]}

    monkeypatch.setattr(llm.requests, "post", lambda *a, **k: FakeResponse())

    body = _ask("what are the best picks?", auth_headers)

    assert body["text"] == "Here's the top pick, rephrased."
    assert body["rewritten"] is True
    assert body["picks"] == [
        {"match_id": fixture_data["upcoming"].id, "market": "Total Goals 0.5", "selection": "Over 0.5"}
    ]

    # And a reload via history must keep both, not just what /message returned.
    history = client.get("/api/chat/history", headers=auth_headers).json()
    assistant_row = next(r for r in history if r["role"] == "assistant")
    assert assistant_row["rewritten"] is True
    assert assistant_row["picks"] == body["picks"]


def test_harmful_request_gets_the_responsible_use_answer(db_session, auth_headers, fixture_data):
    body = _ask("just give me a guaranteed win", auth_headers)
    assert body["intent"] == "responsible_use"
    text = body["text"].lower()
    assert "not guarantee" in text or "no such thing as a guaranteed" in text

    # A reachable helpline, not merely a helpline. This asserted begambleaware
    # while the answer handed out a US 1-800 number that cannot be dialled from
    # Ghana, where the members are -- the test passed and the help did not work.
    assert "0800 678 678" in text, "the answer must give a number Ghanaian members can actually call"
    assert "1-800" not in text, "US toll-free numbers do not connect from Ghana"


# --- Persistence and history ---------------------------------------------


def test_exchange_is_persisted_for_the_user(db_session, auth_headers, fixture_data):
    _ask("arsenal vs chelsea", auth_headers)

    response = client.get("/api/chat/history", headers=auth_headers)
    assert response.status_code == 200
    rows = response.json()
    assert [r["role"] for r in rows] == ["user", "assistant"]
    assert rows[0]["content"] == "arsenal vs chelsea"
    assert rows[1]["intent"] == "match_prediction"


def test_history_returns_oldest_first_and_keeps_the_newest_when_limited(db_session, auth_headers):
    for i in range(4):
        _ask(f"hello number {i}", auth_headers)

    rows = client.get("/api/chat/history?limit=2", headers=auth_headers).json()
    assert len(rows) == 2
    # Limit keeps the most recent exchange, displayed in reading order.
    assert rows[0]["role"] == "user"
    assert "number 3" in rows[0]["content"]


def test_clear_history_removes_only_that_users_messages(db_session, auth_headers):
    _ask("hello", auth_headers)
    assert client.delete("/api/chat/history", headers=auth_headers).json()["deleted"] == 2
    assert client.get("/api/chat/history", headers=auth_headers).json() == []
    assert db_session.query(ChatMessage).count() == 0


# --- Validation and limits -----------------------------------------------


def test_empty_message_is_rejected(db_session, auth_headers):
    response = client.post("/api/chat/message", json={"message": "   "}, headers=auth_headers)
    assert response.status_code == 400


def test_overlong_message_is_rejected(db_session, auth_headers):
    response = client.post("/api/chat/message", json={"message": "a" * 1001}, headers=auth_headers)
    assert response.status_code == 400


def test_chat_is_rate_limited(db_session, auth_headers):
    settings = get_settings()
    rate_limit.reset()
    for _ in range(settings.chat_rate_limit_messages):
        assert client.post("/api/chat/message", json={"message": "hi"}, headers=auth_headers).status_code == 200

    blocked = client.post("/api/chat/message", json={"message": "hi"}, headers=auth_headers)
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers


# --- Streaming ------------------------------------------------------------


def test_stream_chunks_reassemble_into_the_full_answer(db_session, auth_headers, fixture_data):
    """Concatenating the streamed chunks must reproduce the answer exactly --
    otherwise the UI shows subtly different text than the stored message."""

    with client.stream(
        "POST", "/api/chat/stream", json={"message": "arsenal vs chelsea"}, headers=auth_headers
    ) as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())

    chunks: list[str] = []
    done_payload = None
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        event = next((l[len("event: "):] for l in block.splitlines() if l.startswith("event: ")), None)
        data = next((l[len("data: "):] for l in block.splitlines() if l.startswith("data: ")), None)
        if event == "chunk":
            chunks.append(json.loads(data)["text"])
        elif event == "done":
            done_payload = json.loads(data)

    assert done_payload is not None
    assert "".join(chunks) == done_payload["text"]
    assert done_payload["intent"] == "match_prediction"


def test_stream_rejects_an_invalid_message_before_streaming_starts(db_session, auth_headers):
    """A 400 must arrive as a 400, not as a 200 whose body happens to contain
    an error event -- the status code is already sent once streaming begins."""

    response = client.post("/api/chat/stream", json={"message": ""}, headers=auth_headers)
    assert response.status_code == 400
