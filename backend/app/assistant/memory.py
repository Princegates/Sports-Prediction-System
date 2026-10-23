"""Conversation memory: let a follow-up lean on what was just discussed.

A person does not repeat themselves. After "Arsenal vs Chelsea" they ask "why
is this favored?", "and the second half?" or "what about their form?"; after
"best picks today" they ask "what about tomorrow?" or "and in La Liga?". Parsed
alone, each of those is a question about nothing -- ``nlu`` has no match to
explain and no day to rank -- so they fell back to generic picks or the menu.

This module reads the user's own recent history (the ``ChatMessage`` rows the
chat already stores; nothing new is persisted) and fills in only what the new
message left out:

* the **match** last discussed, when the previous answer was about exactly one;
* the **question** last asked -- its intent, market, league and day -- when the
  new message is shaped like a follow-up ("and ...", "what about ...", a few
  words with no intent of their own).

Anything the new message does state wins over the memory, and memory older than
``WINDOW`` is ignored, so a fresh question an hour later is never answered
about a match from before. What was carried over is reported on the answer
("Following on from Arsenal vs Chelsea"), so a wrong inheritance is visible and
the user can simply name what they meant.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, replace

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.assistant import nlu
from app.assistant.nlu import Intent, ParsedQuery
from app.db.models import ChatMessage
from app.timeutil import utcnow

WINDOW = dt.timedelta(minutes=30)
# How many of the user's last messages are folded into "what we were talking
# about" -- enough for "best picks" -> "tomorrow?" -> "in la liga?".
LOOKBACK = 4

_FOLLOW_UP = re.compile(
    r"^\s*(?:and|also|what about|how about|what of|and what about|now|then|ok(?:ay)?|so|same|what if)\b"
)
_REFERS_BACK = re.compile(
    r"\b(?:that|this|the same|those) (?:match|game|fixture|one|tie)\b|\bthat one\b|\bboth teams\b"
)
# Intents that are only answerable about one specific match.
_MATCH_SCOPED = {
    Intent.EXPLAIN_REASONING, Intent.RISKS, Intent.CORRECT_SCORE, Intent.WHAT_CHANGED, Intent.MATCH_PREDICTION,
}
# Intents worth continuing: a follow-up to a greeting or a refusal is not
# "the same question again, differently".
_CONTINUABLE = {
    Intent.BEST_PICKS, Intent.TODAYS_CARD, Intent.GENERATE_SELECTIONS, Intent.TEAM_FORM,
    Intent.MATCH_PREDICTION, Intent.HEAD_TO_HEAD, Intent.COMPARE_TEAMS, Intent.LIVE_STATUS,
}


@dataclass
class Memory:
    query: ParsedQuery | None = None  # the question we were last on
    match_id: int | None = None  # the single match the last answer was about
    match_label: str | None = None


def is_follow_up(text: str, parsed: ParsedQuery) -> bool:
    """Shaped like a continuation rather than a question of its own: it opens
    with "and"/"what about"/..., or it is a few words that matched no intent."""

    if _FOLLOW_UP.search(text):
        return True
    return parsed.intent == Intent.UNKNOWN and len(text.split()) <= 4


def needs_memory(text: str, parsed: ParsedQuery, context_match_id: int | None) -> bool:
    """Whether looking at history could change the answer at all -- so a
    message that stands on its own costs no extra queries."""

    if context_match_id is None and not parsed.teams and (
        nlu.intent_if_match_open(text) in _MATCH_SCOPED or parsed.market_names or _REFERS_BACK.search(text)
    ):
        return True
    return is_follow_up(text, parsed)


def recall(db: Session, user_id: int, now: dt.datetime | None = None) -> Memory:
    """What this user was last talking about, from their own recent messages."""

    now = now or utcnow()
    rows = list(
        db.execute(
            select(ChatMessage)
            .where(ChatMessage.user_id == user_id, ChatMessage.created_at >= now - WINDOW)
            .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
            .limit(LOOKBACK * 2)
        ).scalars()
    )
    rows.reverse()
    memory = Memory()

    # The question we were on: fold the last few user messages oldest-first, a
    # follow-up among them refining the one before it rather than replacing it.
    for row in (r for r in rows if r.role == "user"):
        parsed = nlu.parse(db, row.content, context_match_id=row.context_match_id, now=now)
        if memory.query is not None and is_follow_up(row.content, parsed):
            parsed = _merge(parsed, memory.query, row.content)
        memory.query = parsed

    # The match we were on: the last answer, if it cited exactly one.
    last_answer = next((r for r in reversed(rows) if r.role == "assistant"), None)
    if last_answer is not None:
        matches = [s for s in (last_answer.sources or []) if s.get("kind") == "match" and s.get("ref") is not None]
        if len(matches) == 1:
            memory.match_id = int(matches[0]["ref"])
            memory.match_label = matches[0].get("label")
    return memory


def _merge(current: ParsedQuery, earlier: ParsedQuery, text: str) -> ParsedQuery:
    """``current`` with whatever it left unsaid taken from ``earlier``."""

    intent = current.intent
    # "what about tomorrow?" matches the day-card intent on its own, and a bare
    # market matches best-picks; in a follow-up they are refinements of the
    # earlier question, not new ones.
    refines = current.intent in (Intent.UNKNOWN, Intent.TODAYS_CARD) or (
        current.intent == Intent.BEST_PICKS and not current.teams and bool(_FOLLOW_UP.search(text))
    )
    if earlier.intent in _CONTINUABLE and (
        refines or (current.teams and current.intent in (Intent.TEAM_FORM, Intent.MATCH_PREDICTION)
                    and earlier.intent in (Intent.TEAM_FORM, Intent.MATCH_PREDICTION))
    ):
        intent = earlier.intent

    return replace(
        current,
        intent=intent,
        teams=current.teams or earlier.teams,
        league=current.league or earlier.league,
        date_from=current.date_from or earlier.date_from,
        date_to=current.date_to or earlier.date_to,
        market=current.market or earlier.market,
        market_names=current.market_names or earlier.market_names,
        market_label=current.market_label or earlier.market_label,
        selection_side=current.selection_side or earlier.selection_side,
        selection_count=current.selection_count or earlier.selection_count,
        probability_floor=(
            current.probability_floor if current.probability_floor is not None else earlier.probability_floor
        ),
        risk_level=current.risk_level or earlier.risk_level,
    )


def carry_over(
    db: Session,
    parsed: ParsedQuery,
    text: str,
    memory: Memory,
    now: dt.datetime | None = None,
) -> ParsedQuery:
    """``parsed`` completed from ``memory``; returned unchanged when there is
    nothing to complete. ``carried`` lists what was inherited, for the answer
    to show."""

    carried: list[str] = []

    # The match: re-read the message as if that match were open, which is what
    # the UI's own "ask about this match" button does.
    if (
        parsed.context_match_id is None and not parsed.teams and memory.match_id is not None
        and (nlu.intent_if_match_open(text) in _MATCH_SCOPED or parsed.market_names or _REFERS_BACK.search(text))
    ):
        reread = nlu.parse(db, text, context_match_id=memory.match_id, now=now)
        if reread.intent in _MATCH_SCOPED:
            return replace(reread, carried=[memory.match_label or "the last match"])

    if memory.query is not None and is_follow_up(text, parsed):
        merged = _merge(parsed, memory.query, text)
        if merged != parsed:
            bits = []
            if merged.intent != parsed.intent:
                bits.append(merged.intent.value.replace("_", " "))
            if merged.teams and not parsed.teams:
                bits.append(" vs ".join(t.name for t in merged.teams))
            if merged.league and not parsed.league:
                bits.append(merged.league)
            if merged.market_label and not parsed.market_label:
                bits.append(merged.market_label)
            carried = bits
            return replace(merged, carried=carried) if carried else merged
    return parsed
