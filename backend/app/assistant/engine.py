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
from dataclasses import asdict, replace

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.timeutil import utcnow
from app import app_settings
from app.assistant import agent, llm, memory, nlu, responder
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


def llm_quota_left(db: Session, user_id: int, now: dt.datetime | None = None) -> bool:
    """Whether ``user_id`` may still use the optional LLM today
    (``assistant_llm_daily_per_user`` calls per rolling 24 hours). Past it,
    answers come from the grounded pipeline alone -- same content, plainer
    phrasing -- so a heavy user, or a script, can't run up a provider bill
    or burn a free tier's shared daily allowance for everyone."""

    limit = int(app_settings.get_value(db, "assistant_llm_daily_per_user") or 0)
    now = now or utcnow()
    used = db.execute(
        select(func.count(ChatMessage.id)).where(
            ChatMessage.user_id == user_id,
            ChatMessage.role == "assistant",
            or_(ChatMessage.rewritten.is_(True), ChatMessage.general_chat.is_(True)),
            ChatMessage.created_at >= now - dt.timedelta(hours=24),
        )
    ).scalar_one()
    return used < limit


def compose(
    db: Session,
    message: str,
    context_match_id: int | None = None,
    now: dt.datetime | None = None,
    allow_rewrite: bool = True,
    user_id: int | None = None,
) -> Answer:
    """Build the answer without touching chat history. Separated from
    ``answer`` so tests (and the streaming endpoint) can exercise the
    reasoning path without writing rows. ``user_id``, when given, applies
    that user's daily LLM quota (see ``llm_quota_left``)."""

    text = _validate(message)
    if allow_rewrite and user_id is not None:
        # Smart mode first, when it is switched on and within its daily limits:
        # Claude works out the question and looks the facts up itself. Anything
        # short of a clean answer returns None and the built-in pipeline below
        # answers exactly as it always has.
        smart = agent.run(db, text, user_id=user_id, context_match_id=context_match_id, now=now)
        if smart is not None:
            return smart
    parsed = nlu.parse(db, text, context_match_id=context_match_id, now=now)
    if user_id is not None and memory.needs_memory(text, parsed, context_match_id):
        parsed = memory.carry_over(db, parsed, text, memory.recall(db, user_id, now), now=now)
    result = responder.respond(db, parsed, now=now)

    if allow_rewrite and llm.is_enabled(db) and (user_id is None or llm_quota_left(db, user_id)):
        if result.intent == nlu.Intent.UNKNOWN:
            # Nothing grounded to rewrite -- let the LLM answer the message
            # itself rather than only ever handing back the capability menu.
            general_text = llm.answer_general_question(db, text)
            if general_text:
                result = replace(result, text=general_text, general_chat=True)
        else:
            rewritten_text = llm.rewrite(db, result.text, text)
            if rewritten_text:
                # The grounded text stays the source of truth for storage;
                # only the delivered prose changes. dataclasses.replace()
                # rather than rebuilding Answer(...) field by field, so a
                # field added to Answer later can't be silently dropped
                # here the way one already was.
                result = replace(result, text=rewritten_text, rewritten=True)
    notes = []
    if parsed.carried:
        notes.append("*Following on from: " + "; ".join(parsed.carried) + ".*")
    if parsed.assumed:
        notes.append("*I read " + "; ".join(f"'{a}' as '{b}'" for a, b in parsed.assumed) + ".*")
    if notes and not result.general_chat:
        # Show what was guessed or carried over (see understanding, memory), so
        # a wrong guess is visible. Added after any rewrite so it is never lost.
        result = replace(result, text="\n".join(notes) + f"\n\n{result.text}")
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
        rewritten=result.rewritten,
        general_chat=result.general_chat,
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
    result = compose(db, text, context_match_id=context_match_id, now=now, user_id=user_id)
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
    result = compose(db, text, context_match_id=context_match_id, now=now, user_id=user_id)
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
