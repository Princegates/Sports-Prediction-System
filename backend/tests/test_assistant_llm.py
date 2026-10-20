"""Unit tests for the optional LLM extension. It does two distinct things
with the same HTTP plumbing -- rewrite a grounded answer's phrasing, or
answer a question the grounded pipeline couldn't match to anything -- and
they must never be confused for each other: one is only ever handed text
this system already produced and must not add facts to it, the other has no
grounded text to be faithful to at all. See llm.py's own module docstring.
"""

from __future__ import annotations

from app import app_settings
from app.assistant import llm


def _configure(db_session):
    app_settings.set_values(
        db_session,
        {
            "assistant_llm_enabled": True,
            "assistant_llm_base_url": "https://example-llm.test/v1",
            "assistant_llm_model": "test-model",
        },
    )


class _FakeResponse:
    def __init__(self, content: str):
        self._content = content

    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


def test_answer_general_question_is_none_when_disabled(db_session):
    assert llm.answer_general_question(db_session, "hi there") is None


def test_answer_general_question_returns_the_models_reply(db_session, monkeypatch):
    _configure(db_session)
    captured = {}

    def fake_post(url, json, headers, timeout):
        captured["url"] = url
        captured["json"] = json
        return _FakeResponse("Just a friendly hello back!")

    monkeypatch.setattr(llm.requests, "post", fake_post)

    result = llm.answer_general_question(db_session, "hi, who are you?")

    assert result == "Just a friendly hello back!"
    assert captured["url"] == "https://example-llm.test/v1/chat/completions"
    assert captured["json"]["messages"][0]["content"] == llm.GENERAL_SYSTEM_PROMPT
    assert captured["json"]["messages"][1]["content"] == "hi, who are you?"


def test_answer_general_question_falls_back_to_none_on_failure(db_session, monkeypatch):
    _configure(db_session)

    def fake_post(*a, **k):
        raise llm.requests.RequestException("boom")

    monkeypatch.setattr(llm.requests, "post", fake_post)

    assert llm.answer_general_question(db_session, "hi") is None


def test_rewrite_and_general_question_use_different_system_prompts(db_session, monkeypatch):
    _configure(db_session)
    prompts = []

    def fake_post(url, json, headers, timeout):
        prompts.append(json["messages"][0]["content"])
        return _FakeResponse("ok")

    monkeypatch.setattr(llm.requests, "post", fake_post)

    llm.rewrite(db_session, "grounded text", "the user's question")
    llm.answer_general_question(db_session, "the user's question")

    assert prompts[0] == llm.REWRITE_SYSTEM_PROMPT
    assert prompts[1] == llm.GENERAL_SYSTEM_PROMPT
    assert prompts[0] != prompts[1]


def test_llm_use_is_capped_per_user_per_day(db_session, monkeypatch):
    """Past the daily quota, answers fall back to the grounded pipeline --
    still answered, just not by the (possibly paid) LLM."""

    from app import app_settings
    from app.assistant import engine
    from app.auth.passwords import hash_password
    from app.db.models import User

    user = User(email="chatty@example.com", name="Chatty", password_hash=hash_password("pw-123456"), role="user", status="active")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    app_settings.set_values(db_session, {"assistant_llm_daily_per_user": 2})
    monkeypatch.setattr(llm, "is_enabled", lambda db: True)
    calls = []
    monkeypatch.setattr(llm, "answer_general_question", lambda db, text: calls.append(text) or "An LLM answer.")
    monkeypatch.setattr(llm, "rewrite", lambda db, grounded, text: calls.append(text) or "An LLM rewrite.")

    results = [engine.answer(db_session, user.id, f"what is the offside rule, version {i}?")[0] for i in range(3)]

    assert [r.general_chat or r.rewritten for r in results] == [True, True, False]
    assert len(calls) == 2
