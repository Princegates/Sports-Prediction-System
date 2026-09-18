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

from app.api import rate_limit
from app.auth.passwords import hash_password
from app.auth.tokens import create_token
from app.config import get_settings
from app.db.models import ChatMessage, Match, ModelMetric, Prediction, Team, User
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
