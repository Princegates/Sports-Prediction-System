"""The caller's address for the request being served, so code far from the
route (the audit log's insert) can record it without every one of its ~30
call sites passing a Request through.

Set once per request by a middleware in app.main (a contextvar is copied
into the worker thread a sync endpoint runs on, and into the task Starlette
runs the app in). Outside a request -- the scheduler loops, CLI scripts --
it is simply unset and the column stays NULL.
"""

from __future__ import annotations

from contextvars import ContextVar

_client_ip: ContextVar[str | None] = ContextVar("client_ip", default=None)


def set_client_ip(ip: str | None) -> None:
    _client_ip.set((ip or "")[:64] or None)


def current_client_ip() -> str | None:
    return _client_ip.get()
