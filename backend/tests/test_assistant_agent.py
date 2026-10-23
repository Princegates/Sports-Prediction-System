"""Smart mode (agent.py): Claude works out the question and looks the facts up
through tools. The API is a scripted fake, so these tests pin down what this
code does with it -- the tool loop, that numbers are checked against what the
tools returned, and above all that anything short of a clean answer falls back
to the built-in pipeline rather than failing the chat or spending unboundedly."""

from __future__ import annotations

import datetime as dt
import json
from types import SimpleNamespace

import anthropic
import pytest

from app import app_settings
from app.assistant import agent, engine
from app.assistant.nlu import Intent
from app.db.models import ChatMessage
from app.timeutil import utcnow
from tests.test_chat_api import _ask, _with_half_time_rates, fixture_data  # noqa: F401  (fixture re-export)


# --- a scripted stand-in for the Claude API -------------------------------------------------


def text(t):
    return SimpleNamespace(type="text", text=t)


def tool(name, tool_id="toolu_1", **args):
    return SimpleNamespace(type="tool_use", id=tool_id, name=name, input=args)


def reply(*blocks, stop="end_turn"):
    return SimpleNamespace(
        stop_reason=stop, content=list(blocks), stop_details=None,
        usage=SimpleNamespace(input_tokens=100, output_tokens=50, cache_read_input_tokens=0),
    )


class FakeClaude:
    """Plays back ``script`` one response per call and records every request."""

    def __init__(self, *script):
        self.script = list(script)
        self.requests: list[dict] = []
        self.beta_requests: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._beta_create))

    def _next(self, kwargs):
        # The messages list is mutated as the loop goes; keep what was sent at this moment.
        snapshot = {**kwargs, "messages": [dict(m) for m in kwargs["messages"]]}
        self.requests.append(snapshot)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def _create(self, **kwargs):
        return self._next(kwargs)

    def _beta_create(self, **kwargs):
        self.beta_requests.append({k: v for k, v in kwargs.items() if k in ("betas", "fallbacks")})
        kwargs.pop("betas", None)
        kwargs.pop("fallbacks", None)
        return self._next(kwargs)


@pytest.fixture(autouse=True)
def _reset_module_state():
    agent._fallbacks_rejected = False
    yield
    agent._fallbacks_rejected = False


def enable(db, fake, **settings):
    app_settings.set_values(db, {
        "assistant_agent_enabled": True, "assistant_agent_api_key": "sk-ant-test",
        "assistant_agent_model": "claude-sonnet-4-6", **settings,
    })
    return fake


@pytest.fixture()
def claude(db_session, monkeypatch):
    def make(*script, **settings):
        fake = FakeClaude(*script)
        enable(db_session, fake, **settings)
        monkeypatch.setattr(agent, "_client", lambda values: fake)
        return fake
    return make


def run(db, message="hi", user_id=1, **kw):
    return agent.run(db, message, user_id=user_id, **kw)


# --- when it does not run --------------------------------------------------------------------


def test_it_is_off_by_default(db_session):
    assert run(db_session) is None


def test_it_needs_a_key(db_session):
    app_settings.set_values(db_session, {"assistant_agent_enabled": True})
    assert run(db_session) is None


# --- answering --------------------------------------------------------------------------------


def test_an_answer_with_no_lookups_is_returned_with_its_followups_split_off(db_session, claude):
    claude(reply(text("Hello! Ask me about any fixture.\n\nFollow-ups: What's on today? | Best picks tomorrow | How accurate are you?")))

    answer = run(db_session, "hi")

    assert answer.text == "Hello! Ask me about any fixture."
    assert answer.suggestions == ["What's on today?", "Best picks tomorrow", "How accurate are you?"]
    assert answer.intent == Intent.AGENT and answer.rewritten is True


def test_it_looks_the_match_up_through_a_tool_and_answers_from_the_result(db_session, claude, fixture_data):
    fake = claude(
        reply(tool("get_match", team="arsnal", opponent="chelse"), stop="tool_use"),  # misspelt, as typed
        reply(text("Arsenal are favoured at **52%**, and both teams score at 61%.\n\nFollow-ups: Why? | Risks?")),
    )

    answer = run(db_session, "will arsnal beat chelse")

    assert "52%" in answer.text and "caution" not in answer.text
    # The second request carries the real tool result back, with the stored numbers in it.
    sent = fake.requests[1]["messages"][-1]
    assert sent["role"] == "user" and sent["content"][0]["type"] == "tool_result"
    result = json.loads(sent["content"][0]["content"])
    assert result["home_team"] == "Arsenal" and result["prediction"]["home_win_pct"] == 52.0
    assert "is_error" not in sent["content"][0]
    # Sources come from what the tools returned, not from what the model says.
    assert [(s.kind, s.label) for s in answer.sources] == [("match", "Arsenal vs Chelsea")]


def test_several_tool_results_go_back_in_one_message(db_session, claude, fixture_data):
    fake = claude(
        reply(tool("get_match", "t1", team="Arsenal"), tool("team_form", "t2", team="Chelsea"), stop="tool_use"),
        reply(text("Done.")),
    )

    run(db_session, "compare")

    results = fake.requests[1]["messages"][-1]["content"]
    assert [r["tool_use_id"] for r in results] == ["t1", "t2"]


def test_a_tool_that_cannot_help_returns_a_readable_error_and_the_model_recovers(db_session, claude, fixture_data):
    fake = claude(
        reply(tool("get_match", team="Barcelona"), stop="tool_use"),
        reply(text("I don't have Barcelona in the database.")),
    )

    answer = run(db_session, "barcelona next game")

    result = fake.requests[1]["messages"][-1]["content"][0]
    assert result["is_error"] is True and "No team in the database matches" in result["content"]
    assert answer.text.startswith("I don't have Barcelona")


def test_market_probabilities_come_from_the_registry_not_the_model(db_session, claude, fixture_data):
    _with_half_time_rates(db_session, fixture_data)
    match_id = fixture_data["upcoming"].id
    fake = claude(
        reply(tool("get_market_probabilities", match_id=match_id, markets=["2H Total Goals 1.5"]), stop="tool_use"),
        reply(text("Done.")),
    )

    run(db_session, "2nd half goals")

    rows = json.loads(fake.requests[1]["messages"][-1]["content"][0]["content"])["outcomes"]
    assert {r["market"] for r in rows} == {"2H Total Goals 1.5"}
    assert {r["selection"] for r in rows} == {"Over 1.5", "Under 1.5"}


def test_a_slip_request_returns_picks_and_cites_ai_generation(db_session, claude, fixture_data):
    claude(
        reply(tool("build_slip", legs=3, min_probability_pct=60), stop="tool_use"),
        reply(text("One leg qualifies.")),
    )

    answer = run(db_session, "build me a 3 leg slip")

    assert answer.picks and answer.picks[0].match_id == fixture_data["upcoming"].id
    assert ("page", "AI Generation") in [(s.kind, s.label) for s in answer.sources]


def test_best_picks_for_a_market_honours_the_over_side(db_session, claude, fixture_data):
    _with_half_time_rates(db_session, fixture_data)
    fake = claude(
        reply(tool("best_picks", markets=["2H Total Goals 0.5"], side="Over"), stop="tool_use"),
        reply(text("Done.")),
    )

    run(db_session, "2nd half over 0.5")

    picks = json.loads(fake.requests[1]["messages"][-1]["content"][0]["content"])["picks"]
    assert picks and all(p["selection"].startswith("Over") for p in picks)


def test_the_conversation_so_far_is_sent_and_a_dangling_question_dropped(db_session, claude, fixture_data):
    user_id = 1
    now = utcnow()
    for role, content, ago in [("user", "arsenal vs chelsea", 6), ("assistant", "Arsenal are 52% favourites.", 5),
                               ("user", "never answered", 1)]:
        db_session.add(ChatMessage(user_id=user_id, role=role, content=content, created_at=now - dt.timedelta(minutes=ago)))
    db_session.commit()
    fake = claude(reply(text("Because of home advantage.")))

    run(db_session, "why?", user_id=user_id)

    messages = fake.requests[0]["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert messages[0]["content"] == "arsenal vs chelsea"
    assert messages[-1]["content"].endswith("why?") and "Today is" in messages[-1]["content"]


def test_the_open_match_is_named_to_the_model(db_session, claude, fixture_data):
    fake = claude(reply(text("ok")))

    run(db_session, "why is this favoured?", context_match_id=fixture_data["upcoming"].id)

    assert f"match_id {fixture_data['upcoming'].id}" in fake.requests[0]["messages"][-1]["content"]


# --- the numbers check -------------------------------------------------------------------------


def test_an_invented_figure_gets_one_correction_round(db_session, claude, fixture_data):
    fake = claude(
        reply(tool("get_match", team="Arsenal", opponent="Chelsea"), stop="tool_use"),
        reply(text("Arsenal win 93% of the time.")),  # not in anything the tool returned
        reply(text("Arsenal are favoured at 52%.")),
    )

    answer = run(db_session, "arsenal?")

    assert answer.text == "Arsenal are favoured at 52%." and "caution" not in answer.text
    assert "93%" in fake.requests[2]["messages"][-1]["content"]


def test_a_figure_that_stays_unverified_is_flagged_not_passed_off_as_grounded(db_session, claude, fixture_data):
    claude(
        reply(tool("get_match", team="Arsenal", opponent="Chelsea"), stop="tool_use"),
        reply(text("Arsenal win 93%.")),
        reply(text("Arsenal win 93%.")),
    )

    answer = run(db_session, "arsenal?")

    assert "treat them with caution" in answer.text


def test_the_numbers_check_allows_rounding_complements_and_the_users_own_numbers():
    evidence = [57.3, 52.0]

    assert agent.unverified_percentages("57%, 57.3% and 52%", evidence) == []
    assert agent.unverified_percentages("so Under is 43%", evidence) == []  # 100 - 57.3, rounded
    assert agent.unverified_percentages("at least 70%", evidence, extra="slips with at least 70% chance") == []
    assert agent.unverified_percentages("a 0% or 100% chance", evidence) == []
    assert agent.unverified_percentages("about 91%", evidence) == ["91%"]


# --- failing safe --------------------------------------------------------------------------------


def test_a_refusal_falls_back(db_session, claude):
    claude(reply(stop="refusal"))
    assert run(db_session) is None


def test_an_api_error_falls_back(db_session, claude):
    claude(anthropic.APIConnectionError(request=SimpleNamespace()))
    assert run(db_session) is None


def test_an_unexpected_crash_falls_back(db_session, claude):
    claude(RuntimeError("boom"))
    assert run(db_session) is None


def test_an_empty_answer_falls_back(db_session, claude):
    claude(reply())
    assert run(db_session) is None


def test_a_run_that_never_stops_calling_tools_is_cut_off_and_made_to_answer(db_session, claude, fixture_data):
    script = [reply(tool("list_leagues", f"t{i}"), stop="tool_use") for i in range(agent.MAX_STEPS - 1)]
    fake = claude(*script, reply(text("Here is what I found.")))

    answer = run(db_session, "keep looking")

    assert answer.text == "Here is what I found."
    assert fake.requests[-1]["tool_choice"] == {"type": "none"}  # the last step may not call tools
    assert all("tool_choice" not in r for r in fake.requests[:-1])
    assert len(fake.requests) == agent.MAX_STEPS


def test_an_unknown_tool_is_an_error_result_not_a_crash(db_session, claude):
    fake = claude(reply(tool("delete_everything"), stop="tool_use"), reply(text("Sorry.")))

    run(db_session)

    assert fake.requests[1]["messages"][-1]["content"][0]["is_error"] is True


# --- spending limits -------------------------------------------------------------------------------


def _agent_answers(db, user_id, n):
    for _ in range(n):
        db.add(ChatMessage(user_id=user_id, role="assistant", content="x", intent="agent", created_at=utcnow()))
    db.commit()


def test_a_user_past_their_daily_limit_falls_back(db_session, claude):
    claude(reply(text("hi")), assistant_agent_daily_per_user=2)
    _agent_answers(db_session, 1, 2)

    assert run(db_session, user_id=1) is None
    assert agent.within_limits(db_session, 2)  # someone else is unaffected


def test_the_whole_site_daily_cap_applies_to_everyone(db_session, claude):
    claude(reply(text("hi")), assistant_agent_daily_total=3)
    _agent_answers(db_session, 7, 3)

    assert run(db_session, user_id=1) is None


def test_old_answers_do_not_count_against_todays_limit(db_session, claude):
    claude(reply(text("hi")), assistant_agent_daily_per_user=1)
    db_session.add(ChatMessage(user_id=1, role="assistant", content="x", intent="agent", created_at=utcnow() - dt.timedelta(hours=30)))
    db_session.commit()

    assert run(db_session, user_id=1) is not None


# --- what is sent to the API ----------------------------------------------------------------------------


def test_effort_is_sent_only_to_models_that_accept_it_and_sampling_never(db_session, claude):
    fake = claude(reply(text("a")), assistant_agent_model="claude-sonnet-5-5", assistant_agent_effort="low")
    run(db_session)
    sent = fake.requests[0]
    assert sent["output_config"] == {"effort": "low"}
    assert not {"temperature", "top_p", "top_k", "thinking"} & set(sent)

    fake = claude(reply(text("a")), assistant_agent_model="claude-haiku-4-5")
    run(db_session)
    assert "output_config" not in fake.requests[0]


def test_the_prompt_and_tools_are_stable_so_they_can_be_cached(db_session, claude):
    fake = claude(reply(text("a")), reply(text("b")))
    run(db_session)
    run(db_session)

    first, second = fake.requests
    assert first["system"] == second["system"] and first["tools"] == second["tools"]
    assert first["system"][0]["cache_control"] == {"type": "ephemeral"}
    names = [t["name"] for t in first["tools"]]
    assert names == sorted(names)


def test_refusal_fallbacks_are_requested_for_models_that_support_them_and_dropped_if_rejected(db_session, claude):
    fake = claude(reply(text("a")), assistant_agent_model="claude-opus-5-5")
    run(db_session)
    assert fake.beta_requests == [{"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}]

    # A deployment that rejects the opt-in: the request is retried without it, once and for good.
    bad = anthropic.BadRequestError("no fallbacks here", response=SimpleNamespace(request=None, status_code=400, headers={}), body=None)
    fake = claude(bad, reply(text("b")), reply(text("c")), assistant_agent_model="claude-opus-5-5")
    assert run(db_session).text == "b"
    run(db_session)
    assert len(fake.beta_requests) == 1


# --- the tool plumbing ---------------------------------------------------------------------------------------


def test_a_huge_tool_result_is_shortened_longest_list_first():
    payload = {"fixtures": [{"match": "x" * 200, "n": i} for i in range(400)], "window": "today"}

    text_ = agent._fit(payload)

    assert len(text_) <= agent.TOOL_RESULT_MAX_CHARS
    assert json.loads(text_)["window"] == "today"


def test_followups_are_split_from_the_end_of_an_answer_only():
    assert agent._split_followups("Answer.\nFollow-ups: a | b") == ("Answer.", ["a", "b"])
    assert agent._split_followups("No suggestions here.") == ("No suggestions here.", [])


# --- in the chat ------------------------------------------------------------------------------------------------


def test_the_chat_uses_smart_mode_when_on_and_the_pipeline_when_off(db_session, auth_headers, fixture_data, monkeypatch):
    fake = FakeClaude(reply(text("Smart answer.\n\nFollow-ups: Why?")))
    monkeypatch.setattr(agent, "_client", lambda values: fake)

    off = _ask("arsenal vs chelsea", auth_headers)
    assert off["intent"] == "match_prediction"

    enable(db_session, fake)
    on = _ask("arsenal vs chelsea", auth_headers)
    assert on["intent"] == "agent" and on["text"].startswith("Smart answer")
    assert on["suggestions"] == ["Why?"] and on["rewritten"] is True


def test_when_smart_mode_fails_the_chat_still_answers_from_the_pipeline(db_session, auth_headers, fixture_data, monkeypatch):
    fake = FakeClaude(anthropic.APIConnectionError(request=SimpleNamespace()))
    monkeypatch.setattr(agent, "_client", lambda values: fake)
    enable(db_session, fake)

    body = _ask("arsenal vs chelsea", auth_headers)

    assert body["intent"] == "match_prediction" and "52%" in body["text"]


def test_the_agent_settings_appear_in_the_admin_settings():
    keys = set(app_settings.BY_KEY)
    assert {"assistant_agent_enabled", "assistant_agent_api_key", "assistant_agent_model", "assistant_agent_effort",
            "assistant_agent_daily_per_user", "assistant_agent_daily_total", "assistant_agent_timeout"} <= keys
    assert app_settings.BY_KEY["assistant_agent_api_key"].secret is True
