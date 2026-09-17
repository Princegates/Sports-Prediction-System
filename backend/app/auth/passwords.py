"""Password hashing -- stdlib only (PBKDF2-HMAC-SHA256), no extra
dependency for something this small-scale needs. 200k iterations matches
OWASP's current minimum recommendation for PBKDF2-SHA256.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), _ITERATIONS)
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt, digest_hex = stored_hash.split("$", 1)
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), _ITERATIONS)
    return hmac.compare_digest(digest.hex(), digest_hex)
