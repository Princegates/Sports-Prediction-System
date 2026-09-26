"""Optional LLM extension -- **off by default, and the assistant is complete
without it.**

The grounded pipeline (nlu -> retrieval -> responder) already produces every
answer this system can back with real data. This module does two distinct
things with the same LLM connection, and it is important they stay distinct:

1. **Rewrite.** Given an already-composed grounded answer, ask an LLM to
   phrase it more naturally. The system prompt forbids introducing any
   number, team or claim not present in that text -- what it can change is
   how the answer reads, never what it says.
2. **General chat.** When the grounded pipeline found nothing to answer from
   (``Intent.UNKNOWN`` -- no fixture, team, or system capability the message
   matched), let the LLM answer directly instead of only ever handing back a
   capability menu. This is real generation, not a rewrite of grounded text,
   so its own system prompt separately forbids inventing anything about this
   platform's actual data (a team's form, a fixture, a probability) --
   anything needing that must still route back through the grounded pipeline.

If a call fails, times out, or is not configured, the caller keeps whatever
it already had (the grounded text, or the canned "I couldn't match that"
answer). There is no code path where either feature degrades the answer.

Neither of this makes an LLM call trustworthy in the strict sense -- a model
asked not to add facts sometimes adds facts anyway. That is exactly why both
are opt-in, why the grounded text is always what gets stored as the intent's
own truth, and why the UI marks which of the two happened rather than
presenting either as "from the system." Leave it disabled and the feature
costs nothing and risks nothing.

Five settings drive this -- all in the admin Settings panel (group "AI
assistant"), each with an environment-variable fallback of the same name
uppercased (``ASSISTANT_LLM_ENABLED`` and so on; see app/config.py), so a key
rotation or provider swap is a setting, not a redeploy:

    enabled     -- off by default
    base_url    -- any OpenAI-compatible /chat/completions endpoint. Defaults
                   to Google AI Studio's Gemini API, the one mainstream option
                   with a genuine no-card free tier -- get a key at
                   aistudio.google.com/apikey
    model       -- must match the provider, e.g. "gemini-3.5-flash-lite" for
                   the default above (Flash-Lite over the flagship Flash
                   model: 500 free requests/day vs ~20/day, and rephrasing
                   doesn't need the flagship's extra reasoning), or a local
                   model name for Ollama/llama.cpp/LM Studio if you point
                   base_url at one of those instead
    api_key     -- blank is fine for a local server that doesn't check one
    timeout     -- seconds before a rewrite call gives up (falls back to the
                   grounded text either way)
"""

from __future__ import annotations

import logging

import requests
from sqlalchemy.orm import Session

from app import app_settings

logger = logging.getLogger(__name__)

REWRITE_SYSTEM_PROMPT = """You rewrite football-analytics answers to read more naturally.

Absolute constraints:
- Do NOT introduce any number, percentage, team name, date, or factual claim that is not already present in the text you are given.
- Do NOT remove any number or caveat that is present.
- Do NOT present a probability as a certainty, prediction as a guarantee, or offer betting advice.
- Keep the same structure and roughly the same length. Markdown is fine.

Return only the rewritten text."""

# Used only when the grounded pipeline found nothing to answer from -- a
# genuinely different job from REWRITE_SYSTEM_PROMPT above, which is only
# ever handed text this system already produced. This one is handed a raw
# user message and asked to generate a reply from nothing, so its own
# constraints are about scope, not fidelity to an existing text: answer
# what's safe to answer generally, and refuse anything that would require
# this platform's own data to answer honestly.
GENERAL_SYSTEM_PROMPT = """You are Guda, the assistant on Socca Intelligence, a football (soccer) prediction platform.

A user just asked something the platform's own structured football-data system could not match to anything it tracks (a fixture, a team's form, a prediction, the model's own accuracy or methodology). You are being asked to answer it directly and conversationally instead of only handing back "I don't understand."

Absolute constraints:
- Do NOT invent or estimate any fact specific to this platform's own data -- a team's current form, a fixture, a match prediction, a probability, an accuracy figure, or anything else this system would need its database to answer honestly. If the question needs that, say plainly that you don't have that from here and suggest asking it more directly (naming both teams, or "what's on today").
- Do NOT give financial or betting advice, or suggest any bet, pick, or outcome is safe or guaranteed.
- General football knowledge not specific to this platform (rules, history, trivia) is fine to answer directly.
- Keep answers brief and conversational -- this is chat, not a report.

Answer the user's message now."""


def _values_configured(values: dict) -> bool:
    return bool(values["assistant_llm_enabled"] and values["assistant_llm_base_url"])


def _chat_completion(values: dict, system_prompt: str, user_content: str, *, max_tokens: int) -> str | None:
    """Shared plumbing for both rewrite() and answer_general_question(): one
    OpenAI-compatible /chat/completions call, tolerant of any failure. The
    two differ only in what they send and why -- not in how the call is
    made, retried (never -- a slow provider just falls back), or logged."""

    url = values["assistant_llm_base_url"].rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if values["assistant_llm_api_key"]:
        headers["Authorization"] = f"Bearer {values['assistant_llm_api_key']}"

    payload = {
        "model": values["assistant_llm_model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.3,
        "max_tokens": max_tokens,
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=values["assistant_llm_timeout"])
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
    except (requests.RequestException, KeyError, IndexError, ValueError) as exc:
        # Both callers treat this as cosmetic/optional; never fail the
        # user's question over it. The status line alone (e.g. "400 Bad
        # Request") doesn't say *why* -- the provider's own error body does,
        # so log it too, truncated in case it's ever unexpectedly large.
        detail = str(exc)
        error_response = getattr(exc, "response", None)
        if error_response is not None:
            detail = f"{detail} -- body: {error_response.text[:500]}"
        logger.warning("Assistant LLM call unavailable (%s)", detail)
        return None

    text = (content or "").strip()
    return text or None


def is_enabled(db: Session) -> bool:
    values = app_settings.all_values(db)
    return _values_configured(values)


def rewrite(db: Session, grounded_text: str, user_message: str) -> str | None:
    """Return a more fluent version of ``grounded_text``, or ``None`` if the
    rewrite is unavailable for any reason. Callers must treat ``None`` as
    "use the grounded text", never as an error."""

    values = app_settings.all_values(db)
    if not _values_configured(values):
        return None

    user_content = f"The user asked: {user_message}\n\nRewrite this answer:\n\n{grounded_text}"
    return _chat_completion(values, REWRITE_SYSTEM_PROMPT, user_content, max_tokens=900)


def answer_general_question(db: Session, user_message: str) -> str | None:
    """Return a direct, conversational answer to a message the grounded
    pipeline couldn't match to anything (``Intent.UNKNOWN``), or ``None`` if
    unavailable for any reason. Callers must treat ``None`` as "use the
    grounded fallback answer", never as an error -- same contract as
    ``rewrite``, just generating rather than rephrasing."""

    values = app_settings.all_values(db)
    if not _values_configured(values):
        return None

    return _chat_completion(values, GENERAL_SYSTEM_PROMPT, user_message, max_tokens=400)
