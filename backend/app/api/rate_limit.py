"""In-process fixed-window rate limiting for the credential and chat endpoints.

**What this is for:** without it, `/api/auth/login` will happily accept
thousands of password guesses a minute, which turns every weak password in the
user table into a matter of time. A limiter is the difference between "needs a
strong password" and "needs a strong password *and* time nobody has".

**Key on identity first, IP second.** Every IP a request carries in a header
can be forged, and Render's proxy *appends* to a client-sent
``X-Forwarded-For`` rather than replacing it -- so a limit keyed only on the
left-most entry was beaten by sending a different made-up address on each
request. The limits that matter are therefore keyed on something the client
cannot rotate for free: the account email being guessed at (login, status
checks, password resets) or the authenticated user (code redemption, chat,
password change). The per-IP limit stays as a second layer; ``client_ip``
prefers ``CF-Connecting-IP`` (set by Cloudflare at the edge, which overwrites
any client-sent value -- configurable as ``CLIENT_IP_HEADER``) before falling
back to the forwarded chain.

**What it is not:** a distributed limiter. State lives in this process's
memory, so it resets on restart and is counted per-worker. That is the right
trade-off for the single instance this deploys as; the upgrade path, once
there's more than one, is to back ``_HITS`` with a shared store and keep this
interface.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict

from fastapi import HTTPException, Request

from app.config import get_settings

# key -> list of hit timestamps within the current window
_HITS: dict[str, list[float]] = defaultdict(list)
_LOCK = threading.Lock()

# Stop unbounded growth from a spray of one-hit-each keys. Well above any
# plausible concurrent-client count for this deployment size.
_MAX_TRACKED_KEYS = 10_000


def client_ip(request: Request) -> str:
    """Best-effort client address, for the per-IP limit and the audit log.

    An edge-set header wins when present (see module docstring). The
    left-most ``X-Forwarded-For`` entry is the next best guess -- it is what
    Render reports as the client -- but it is client-controlled, which is
    why no limit that matters relies on this alone.
    """

    header = (get_settings().client_ip_header or "").strip().lower()
    if header:
        value = request.headers.get(header)
        if value:
            return value.split(",")[0].strip()
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def client_key(request: Request, scope: str) -> str:
    return f"{scope}:{client_ip(request)}"


def _prune(now: float, window: float) -> None:
    """Drop keys whose every hit has aged out. Called only when the table is
    oversized, so the common path stays O(1) in the number of keys."""

    stale = [key for key, hits in _HITS.items() if not hits or now - hits[-1] > window]
    for key in stale:
        del _HITS[key]


def check(key: str, limit: int, window_seconds: float) -> tuple[bool, float]:
    """Record a hit against ``key``. Returns ``(allowed, retry_after)``.

    A rejected request is *not* recorded, so a client that keeps hammering a
    closed window doesn't extend it indefinitely -- the window still drains on
    schedule.
    """

    now = time.monotonic()
    cutoff = now - window_seconds

    with _LOCK:
        if len(_HITS) > _MAX_TRACKED_KEYS:
            _prune(now, window_seconds)

        hits = [t for t in _HITS[key] if t > cutoff]

        if len(hits) >= limit:
            _HITS[key] = hits
            retry_after = max(0.0, window_seconds - (now - hits[0]))
            return False, retry_after

        hits.append(now)
        _HITS[key] = hits
        return True, 0.0


def peek(key: str, limit: int, window_seconds: float) -> tuple[bool, float]:
    """Like ``check`` but records nothing -- for limits that only count
    failures (a wrong password), checked before the attempt is judged."""

    now = time.monotonic()
    cutoff = now - window_seconds
    with _LOCK:
        hits = [t for t in _HITS.get(key, ()) if t > cutoff]
    if len(hits) >= limit:
        return False, max(0.0, window_seconds - (now - hits[0]))
    return True, 0.0


def _too_many(retry_after: float) -> HTTPException:
    seconds = int(retry_after) + 1
    return HTTPException(
        status_code=429,
        detail=f"Too many attempts. Try again in {seconds} second{'s' if seconds != 1 else ''}.",
        headers={"Retry-After": str(seconds)},
    )


def enforce_key(key: str, limit: int, window_seconds: float) -> None:
    """Raise 429 if ``key`` -- an identity, not an IP -- is over its limit."""

    allowed, retry_after = check(key, limit, window_seconds)
    if not allowed:
        raise _too_many(retry_after)


def enforce(request: Request, scope: str, limit: int, window_seconds: float) -> None:
    """Raise 429 if ``scope`` is over its limit for this client's IP."""

    enforce_key(client_key(request, scope), limit, window_seconds)


def raise_if_limited(key: str, limit: int, window_seconds: float) -> None:
    """Raise 429 if ``key`` is already over its limit, without counting this
    request -- pair with ``check``/``enforce_key`` on the failure path."""

    allowed, retry_after = peek(key, limit, window_seconds)
    if not allowed:
        raise _too_many(retry_after)


def reset() -> None:
    """Clear all counters. For tests -- each one needs a clean window, since
    the state is module-level and would otherwise leak between them."""

    with _LOCK:
        _HITS.clear()
