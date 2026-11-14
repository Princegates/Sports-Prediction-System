"""Password hashing -- stdlib only (PBKDF2-HMAC-SHA256), no extra
dependency for something this small-scale needs.

New hashes use 600,000 iterations (OWASP's current figure for PBKDF2-SHA256)
and record the count: ``pbkdf2_sha256$<iterations>$<salt>$<digest>``. Hashes
from before that are ``<salt>$<digest>`` at 200,000 and still verify;
``needs_rehash`` tells login to replace one with the current format once the
password is known to be right.

``PASSWORD_HASH_ITERATIONS`` overrides the count -- only the test suite sets
it, to keep thousands of hashes fast.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets

_SCHEME = "pbkdf2_sha256"
_LEGACY_ITERATIONS = 200_000
ITERATIONS = int(os.environ.get("PASSWORD_HASH_ITERATIONS") or 600_000)


def _digest(password: str, salt: str, iterations: int) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations).hex()


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    return f"{_SCHEME}${ITERATIONS}${salt}${_digest(password, salt, ITERATIONS)}"


def _parse(stored_hash: str) -> tuple[int, str, str] | None:
    parts = (stored_hash or "").split("$")
    try:
        if len(parts) == 4 and parts[0] == _SCHEME:
            return int(parts[1]), parts[2], parts[3]
        if len(parts) == 2:
            return _LEGACY_ITERATIONS, parts[0], parts[1]
    except ValueError:
        return None
    return None


def verify_password(password: str, stored_hash: str) -> bool:
    parsed = _parse(stored_hash)
    if parsed is None:
        return False
    iterations, salt, digest_hex = parsed
    if not 1 <= iterations <= 10_000_000:
        return False
    try:
        return hmac.compare_digest(_digest(password, salt, iterations), digest_hex)
    except ValueError:  # a salt that isn't hex
        return False


def needs_rehash(stored_hash: str) -> bool:
    parsed = _parse(stored_hash)
    return parsed is None or parsed[0] != ITERATIONS or not stored_hash.startswith(_SCHEME + "$")
