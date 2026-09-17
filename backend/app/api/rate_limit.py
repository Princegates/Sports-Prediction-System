"""In-process fixed-window rate limiting for the credential and chat endpoints.

**What this is for:** without it, `/api/auth/login` will happily accept
thousands of password guesses a minute, which turns every weak password in the
user table into a matter of time. A limiter is the difference between "needs a
strong password" and "needs a strong password *and* time nobody has".

**What it is not:** a distributed limiter. State lives in this process's
memory, so it resets on restart and is counted per-worker. Running four uvicorn
workers means the effective limit is four times the configured one, and a
horizontally scaled deployment shares nothing at all.

That trade-off is deliberate at this scale -- it's a dependency-free 60 lines
that raises the cost of brute force by orders of magnitude today, rather than a
Redis service to stand up (and pay for) before login is protected at all. The
upgrade path, once there's more than one worker, is to back ``_HITS`` with
Redis and keep this interface; nothing else changes.

Client identity is the peer IP, honoring ``X-Forwarded-For`` when present since
any real deployment sits behind a proxy. That header is client-controlled and
therefore spoofable, so an attacker who rotates it evades this. Rotating IPs
defeats IP-based limiting in general; the point is to stop the cheap,
single-source attack, which is the one that actually happens.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict

from fastapi import HTTPException, Request

# key -> list of hit timestamps within the current window
_HITS: dict[str, list[float]] = defaultdict(list)
_LOCK = threading.Lock()

# Stop unbounded growth from a spray of one-hit-each keys. Well above any
# plausible concurrent-client count for this deployment size.
_MAX_TRACKED_KEYS = 10_000


def client_key(request: Request, scope: str) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        # Left-most entry is the original client per convention.
        ip = forwarded.split(",")[0].strip()
    else:
        ip = request.client.host if request.client else "unknown"
    return f"{scope}:{ip}"


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


def enforce(request: Request, scope: str, limit: int, window_seconds: float) -> None:
    """Raise 429 if ``scope`` is over its limit for this client."""

    allowed, retry_after = check(client_key(request, scope), limit, window_seconds)
    if allowed:
        return

    seconds = int(retry_after) + 1
    raise HTTPException(
        status_code=429,
        detail=f"Too many attempts. Try again in {seconds} second{'s' if seconds != 1 else ''}.",
        headers={"Retry-After": str(seconds)},
    )


def reset() -> None:
    """Clear all counters. For tests -- each one needs a clean window, since
    the state is module-level and would otherwise leak between them."""

    with _LOCK:
        _HITS.clear()
