"""Optional LLM rewriter -- **off by default, and the assistant is complete
without it.**

The grounded pipeline (nlu -> retrieval -> responder) already produces the
answer. What an LLM can add here is phrasing: turning a bulleted factor list
into something that reads like a person wrote it. What it must *not* be
allowed to add is facts.

So the contract for this module is narrow on purpose:

- It receives the already-composed grounded answer and is asked to rewrite it.
- The system prompt forbids introducing any number, team or claim not present
  in that text.
- If the call fails, times out, or is not configured, the caller keeps the
  grounded text. There is no code path where a failed rewrite degrades the
  answer.

This still cannot make a rewriter trustworthy in the strict sense -- a model
asked not to add facts sometimes adds facts anyway. That is exactly why it is
opt-in, why the grounded text is what gets stored, and why the UI marks
rewritten answers. Leave it disabled and the feature costs nothing and risks
nothing.

Five settings drive this -- all in the admin Settings panel (group "AI
assistant"), each with an environment-variable fallback of the same name
uppercased (``ASSISTANT_LLM_ENABLED`` and so on; see app/config.py), so a key
rotation or provider swap is a setting, not a redeploy:

    enabled     -- off by default
    base_url    -- any OpenAI-compatible /chat/completions endpoint. Defaults
                   to Google AI Studio's Gemini API, the one mainstream option
                   with a genuine no-card free tier -- get a key at
                   aistudio.google.com/apikey
    model       -- must match the provider, e.g. "gemini-2.5-flash" for the
                   default above, or a local model name for Ollama/llama.cpp/
                   LM Studio if you point base_url at one of those instead
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


def is_enabled(db: Session) -> bool:
    values = app_settings.all_values(db)
    return bool(values["assistant_llm_enabled"] and values["assistant_llm_base_url"])


def rewrite(db: Session, grounded_text: str, user_message: str) -> str | None:
    """Return a more fluent version of ``grounded_text``, or ``None`` if the
    rewrite is unavailable for any reason. Callers must treat ``None`` as
    "use the grounded text", never as an error."""

    values = app_settings.all_values(db)
    if not (values["assistant_llm_enabled"] and values["assistant_llm_base_url"]):
        return None

    url = values["assistant_llm_base_url"].rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if values["assistant_llm_api_key"]:
        headers["Authorization"] = f"Bearer {values['assistant_llm_api_key']}"

    payload = {
        "model": values["assistant_llm_model"],
        "messages": [
            {"role": "system", "content": REWRITE_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"The user asked: {user_message}\n\n"
                    f"Rewrite this answer:\n\n{grounded_text}"
                ),
            },
        ],
        "temperature": 0.3,
        "max_tokens": 900,
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=values["assistant_llm_timeout"])
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
    except (requests.RequestException, KeyError, IndexError, ValueError) as exc:
        # A rewrite is cosmetic; never fail the user's question over it. The
        # status line alone (e.g. "400 Bad Request") doesn't say *why* --
        # the provider's own error body does, so log it too, truncated in
        # case it's ever unexpectedly large.
        detail = str(exc)
        error_response = getattr(exc, "response", None)
        if error_response is not None:
            detail = f"{detail} -- body: {error_response.text[:500]}"
        logger.warning("Assistant LLM rewrite unavailable (%s) -- serving grounded text", detail)
        return None

    text = (content or "").strip()
    return text or None
