"""AI assistant chat endpoints.

Three ways to talk to the assistant:

``POST /api/chat/message``
    Plain request/response. Simplest to consume and what the tests use.
``POST /api/chat/stream``
    Same answer, delivered as Server-Sent Events so the UI can render it
    progressively. SSE rather than WebSockets because the traffic is strictly
    one-directional once the question is asked, and SSE needs no protocol
    upgrade, no extra dependency, and reconnects on its own.
``GET /api/chat/history`` / ``DELETE /api/chat/history``
    The user's own conversation, scoped to their account.

Every route requires a live access grant (app.main gates this whole router on
``require_active_access``) -- the assistant answers from prediction data, so
it's part of the same paywalled surface as the predictions themselves. Chat
history is filtered by ``user_id`` at the query level, so one user's
conversation is not reachable from another's session.
"""

from __future__ import annotations

import json
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.api.rate_limit import enforce
from app.api.schemas import ChatAnswerOut, ChatMessageIn, ChatMessageOut, ChatPickOut, ChatSourceOut
from app.assistant import engine
from app.assistant.responder import PROBABILITY_CAVEAT, Answer
from app.config import get_settings
from app.db.models import ChatMessage, User

router = APIRouter(prefix="/api/chat", tags=["chat"])


def _rate_limit(request: Request) -> None:
    settings = get_settings()
    enforce(
        request,
        "chat",
        settings.chat_rate_limit_messages,
        settings.chat_rate_limit_window_seconds,
    )


def _answer_to_schema(result: Answer, message_id: int | None = None) -> ChatAnswerOut:
    return ChatAnswerOut(
        id=message_id,
        text=result.text,
        intent=result.intent.value,
        sources=[ChatSourceOut(**asdict(s)) for s in result.sources],
        suggestions=result.suggestions,
        includes_probability=result.includes_probability,
        caveat=PROBABILITY_CAVEAT if result.includes_probability else None,
        picks=[ChatPickOut(**asdict(p)) for p in result.picks],
    )


def _row_to_schema(row: ChatMessage) -> ChatMessageOut:
    return ChatMessageOut(
        id=row.id,
        role=row.role,
        content=row.content,
        intent=row.intent,
        context_match_id=row.context_match_id,
        sources=[ChatSourceOut(**s) for s in (row.sources or [])],
        suggestions=row.suggestions or [],
        picks=[ChatPickOut(**p) for p in (row.picks or [])],
        created_at=row.created_at,
    )


@router.post("/message", response_model=ChatAnswerOut)
def send_message(
    payload: ChatMessageIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ChatAnswerOut:
    _rate_limit(request)
    try:
        result, row = engine.answer(
            db,
            user.id,
            payload.message,
            context_match_id=payload.context_match_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _answer_to_schema(result, row.id)


@router.post("/stream")
def stream_message(
    payload: ChatMessageIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Server-Sent Events: a run of ``chunk`` events, then one ``done`` event
    carrying the intent, citations and follow-up suggestions.

    Validation happens before the generator is handed to Starlette -- once
    streaming has begun the status code is already sent, so a 400 raised
    inside the generator would reach the client as a truncated 200.
    """

    _rate_limit(request)
    try:
        engine._validate(payload.message)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    def event_stream():
        try:
            for kind, item in engine.stream_answer(
                db, user.id, payload.message, context_match_id=payload.context_match_id
            ):
                if kind == "chunk":
                    yield f"event: chunk\ndata: {json.dumps({'text': item})}\n\n"
                else:
                    payload_json = _answer_to_schema(item).model_dump_json()
                    yield f"event: done\ndata: {payload_json}\n\n"
        except Exception as exc:  # noqa: BLE001 -- must not hang an open stream
            yield f"event: error\ndata: {json.dumps({'detail': str(exc)})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Stops nginx buffering the stream into one delivery, which would
            # defeat the point of streaming at all.
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/history", response_model=list[ChatMessageOut])
def get_history(
    limit: int = Query(default=50, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ChatMessageOut]:
    return [_row_to_schema(row) for row in engine.history(db, user.id, limit=limit)]


@router.delete("/history")
def delete_history(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    deleted = engine.clear_history(db, user.id)
    return {"deleted": deleted}
