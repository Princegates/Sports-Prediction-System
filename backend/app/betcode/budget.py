"""Keeping a slow bookmaker from tying up the server.

A booking makes several requests to the site (event lists, then one or more
share calls), each with its own timeout -- unbounded in total, and the event
list is fetched under a lock, so one stalled site used to hold every other
booking in that worker for minutes. Two guards:

- ``deadline``: the whole booking gets ``BOOKING_DEADLINE_SECONDS``; every
  request's timeout is cut to what is left (``request_timeout``), and waiting
  for the event-list lock counts against it too (``acquire``).
- ``Breaker``: after two bookings in a row fail to reach a site, the next
  minute of bookings there fail at once instead of each waiting out the
  timeout again.
"""

from __future__ import annotations

import contextlib
import contextvars
import threading
import time

import requests

from app.betcode.sites import BookingCodeError

BOOKING_DEADLINE_SECONDS = 20.0
BREAKER_FAILURES = 2
BREAKER_OPEN_SECONDS = 60.0

_deadline: contextvars.ContextVar[float | None] = contextvars.ContextVar("booking_deadline", default=None)


class DeadlineExceeded(BookingCodeError):
    pass


@contextlib.contextmanager
def deadline(seconds: float = BOOKING_DEADLINE_SECONDS):
    token = _deadline.set(time.monotonic() + seconds)
    try:
        yield
    finally:
        _deadline.reset(token)


def _remaining() -> float | None:
    end = _deadline.get()
    return None if end is None else end - time.monotonic()


def request_timeout(site: str, default: float) -> float:
    left = _remaining()
    if left is None:
        return default
    if left < 0.5:
        raise DeadlineExceeded(f"{site} is taking too long to answer. Try again in a moment.")
    return min(default, left)


def acquire(lock: threading.Lock, site: str) -> None:
    left = _remaining()
    if not lock.acquire(timeout=-1 if left is None else max(0.0, left)):
        raise DeadlineExceeded(f"{site} is busy with other bookings. Try again in a moment.")


class Breaker:
    def __init__(self, site: str) -> None:
        self.site = site
        self._lock = threading.Lock()
        self._failures = 0
        self._open_until = 0.0

    def check(self) -> None:
        if time.monotonic() < self._open_until:
            raise BookingCodeError(f"{self.site} isn't answering right now. Try again in a minute.")

    def record(self, exc: BaseException | None) -> None:
        """Count a booking's outcome: only failing to reach the site (no
        answer, or out of time) counts against it -- a refusal is an answer."""

        unreachable = exc is not None and (
            isinstance(exc, DeadlineExceeded) or isinstance(exc.__cause__, requests.RequestException)
        )
        with self._lock:
            if not unreachable:
                self._failures = 0
                return
            self._failures += 1
            if self._failures >= BREAKER_FAILURES:
                self._open_until = time.monotonic() + BREAKER_OPEN_SECONDS
                self._failures = 0

    def reset(self) -> None:
        with self._lock:
            self._failures = 0
            self._open_until = 0.0
