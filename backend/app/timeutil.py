"""Current UTC time, in the naive form every DateTime column here stores.

``datetime.utcnow()`` is deprecated (and slated for removal), but its
replacement, ``datetime.now(UTC)``, returns an *aware* datetime -- and
comparing aware with naive raises. Every stored timestamp in this schema is
naive UTC, so this keeps that exact shape: same value ``utcnow()`` returned,
without the deprecated call.
"""

from __future__ import annotations

import datetime as dt


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(tzinfo=None)
