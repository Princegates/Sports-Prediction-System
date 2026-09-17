"""Grounded conversational assistant.

The assistant answers questions about fixtures, predictions, teams and the
model's own track record using **only** values read out of this system's
database. It is split into three stages so each one is independently
testable:

``nlu``
    Classify the user's intent and resolve the entities they named (teams,
    leagues, markets, dates) against real rows in the database.
``retrieval``
    Fetch exactly the records that intent needs -- nothing speculative.
``responder``
    Turn those records into prose. Every number in the output traces back to
    a retrieved row, which is what makes hallucinated statistics structurally
    impossible rather than merely unlikely.

``llm`` is an optional final-pass rewriter for people who later want more
fluent phrasing. It is disabled by default and the system is fully functional
without it, so running this assistant costs nothing.
"""

from app.assistant.engine import answer, stream_answer
from app.assistant.nlu import Intent, parse

__all__ = ["answer", "stream_answer", "Intent", "parse"]
