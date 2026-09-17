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
rewritten answers. Leave ``ASSISTANT_LLM_ENABLED`` false and the feature costs
nothing and risks nothing.

To enable later, point it at any OpenAI-compatible ``/chat/completions``
endpoint. A locally-run one (Ollama, llama.cpp's server, LM Studio) keeps the
zero-cost property intact:

    ASSISTANT_LLM_ENABLED=true
    ASSISTANT_LLM_BASE_URL=http://localhost:11434/v1
    ASSISTANT_LLM_MODEL=llama3.2
    ASSISTANT_LLM_API_KEY=       # unused by most local servers
"""

from __future__ import annotations

import logging

import requests

from app.config import get_settings

logger = logging.getLogger(__name__)

REWRITE_SYSTEM_PROMPT = """You rewrite football-analytics answers to read more naturally.

Absolute constraints:
- Do NOT introduce any number, percentage, team name, date, or factual claim that is not already present in the text you are given.
- Do NOT remove any number or caveat that is present.
- Do NOT present a probability as a certainty, prediction as a guarantee, or offer betting advice.
- Keep the same structure and roughly the same length. Markdown is fine.

Return only the rewritten text."""


def is_enabled() -> bool:
    settings = get_settings()
    return bool(settings.assistant_llm_enabled and settings.assistant_llm_base_url)


def rewrite(grounded_text: str, user_message: str) -> str | None:
    """Return a more fluent version of ``grounded_text``, or ``None`` if the
    rewrite is unavailable for any reason. Callers must treat ``None`` as
    "use the grounded text", never as an error."""

    if not is_enabled():
        return None

    settings = get_settings()
    url = settings.assistant_llm_base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if settings.assistant_llm_api_key:
        headers["Authorization"] = f"Bearer {settings.assistant_llm_api_key}"

    payload = {
        "model": settings.assistant_llm_model,
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
        response = requests.post(url, json=payload, headers=headers, timeout=settings.assistant_llm_timeout)
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
    except (requests.RequestException, KeyError, IndexError, ValueError) as exc:
        # A rewrite is cosmetic; never fail the user's question over it.
        logger.warning("Assistant LLM rewrite unavailable (%s) -- serving grounded text", exc)
        return None

    text = (content or "").strip()
    return text or None
