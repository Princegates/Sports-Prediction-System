"""Assistant entry point: parse -> retrieve -> respond, plus persistence.

``answer`` is what the plain request/response endpoint calls. ``stream_answer``
yields the same text in chunks for the SSE endpoint -- the answer is fully
composed before the first chunk is emitted, so streaming here is a
presentation choice (the reply appears progressively instead of landing as a
wall of text) and not a claim that generation is incremental. Chunking on word
boundaries keeps it readable mid-stream.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.assistant import llm, nlu, responder
from app.assistant.responder import Answer
from app.db.models import ChatMessage

MAX_MESSAGE_LENGTH = 1000
HISTORY_LIMIT = 100

# Words per streamed chunk. Small enough to feel progressive, large enough
# that a long answer doesn't become hundreds of SSE frames.
STREAM_CHUNK_WORDS = 4


def _validate(message: str) -> str:
    text = (message or "").strip()
    if not text:
        raise ValueError("Message cannot be empty")
    if len(text) > MAX_MESSAGE_LENGTH:
        raise ValueError(f"Message is too long (limit {MAX_MESSAGE_LENGTH} characters)")
    return text


def compose(
    db: Session,
    message: str,
    context_match_id: int | None = None,
    now: dt.datetime | None = None,
    allow_rewrite: bool = True,
) -> Answer:
    """Build the answer without touching chat history. Separated from
    ``answer`` so tests (and the streaming endpoint) can exercise the
    reasoning path without writing rows."""

    text = _validate(message)
    parsed = nlu.parse(db, text, context_match_id=context_match_id, now=now)
    result = responder.respond(db, parsed, now=now)

    if allow_rewrite and llm.is_enabled():
        rewritten = llm.rewrite(result.text, text)
        if rewritten:
            # The grounded text stays the source of truth for storage; only
            # the delivered prose changes.
            result = Answer(
                text=rewritten,
                intent=result.intent,
                sources=result.sources,
                suggestions=result.suggestions,
                includes_probability=result.includes_probability,
            )
    return result


def _persist(
    db: Session,
    user_id: int,
    user_message: str,
    result: Answer,
    context_match_id: int | None,
) -> ChatMessage:
    db.add(
        ChatMessage(
            user_id=user_id,
            role="user",
            content=user_message,
            context_match_id=context_match_id,
        )
    )
    assistant_row = ChatMessage(
        user_id=user_id,
        role="assistant",
        content=result.text,
        intent=result.intent.value,
        context_match_id=context_match_id,
        sources=[asdict(s) for s in result.sources],
        suggestions=list(result.suggestions),
        picks=[asdict(p) for p in result.picks],
    )
    db.add(assistant_row)
    db.commit()
    db.refresh(assistant_row)
    return assistant_row


def answer(
    db: Session,
    user_id: int,
    message: str,
    context_match_id: int | None = None,
    now: dt.datetime | None = None,
) -> tuple[Answer, ChatMessage]:
    text = _validate(message)
    result = compose(db, text, context_match_id=context_match_id, now=now)
    row = _persist(db, user_id, text, result, context_match_id)
    return result, row


def stream_answer(
    db: Session,
    user_id: int,
    message: str,
    context_match_id: int | None = None,
    now: dt.datetime | None = None,
):
    """Yield ``(kind, payload)`` tuples: ``("chunk", text)`` repeatedly, then
    a single ``("done", Answer)``. The full answer is persisted before the
    first chunk so a client that disconnects mid-stream still has the
    exchange in its history."""

    text = _validate(message)
    result = compose(db, text, context_match_id=context_match_id, now=now)
    _persist(db, user_id, text, result, context_match_id)

    words = result.text.split(" ")
    for i in range(0, len(words), STREAM_CHUNK_WORDS):
        chunk = " ".join(words[i : i + STREAM_CHUNK_WORDS])
        # Re-add the separating space except on the final chunk, so
        # concatenating the chunks reproduces the text exactly.
        if i + STREAM_CHUNK_WORDS < len(words):
            chunk += " "
        yield "chunk", chunk

    yield "done", result


def history(db: Session, user_id: int, limit: int = 50) -> list[ChatMessage]:
    """This user's recent exchanges, oldest first (display order).

    Fetched newest-first then reversed so ``limit`` keeps the *most recent*
    messages rather than the oldest ones.
    """

    rows = list(
        db.execute(
            select(ChatMessage)
            .where(ChatMessage.user_id == user_id)
            .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
            .limit(min(limit, HISTORY_LIMIT))
        ).scalars()
    )
    return list(reversed(rows))


def clear_history(db: Session, user_id: int) -> int:
    rows = db.execute(select(ChatMessage).where(ChatMessage.user_id == user_id)).scalars().all()
    for row in rows:
        db.delete(row)
    db.commit()
    return len(rows)
