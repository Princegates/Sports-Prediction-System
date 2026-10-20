"""The API refuses to boot against a real database while SECRET_KEY is the
default published in this repository -- anyone could forge a session for
any account with it. Run in a subprocess, since the check happens at import.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
DEFAULT_KEY = "dev-secret-change-me-in-production"
# Never reached: the guard runs before the first connection attempt.
UNREACHABLE_POSTGRES = "postgresql://user:pass@127.0.0.1:1/db"


def _import_app(database_url: str, secret_key: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": database_url, "SECRET_KEY": secret_key}
    return subprocess.run(
        [sys.executable, "-c", "import app.main"], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=120
    )


def test_default_key_is_refused_against_a_real_database():
    result = _import_app(UNREACHABLE_POSTGRES, DEFAULT_KEY)
    assert result.returncode != 0
    assert "Refusing to start" in result.stderr


def test_default_key_only_warns_on_local_sqlite(tmp_path):
    result = _import_app(f"sqlite:///{tmp_path}/dev.db", DEFAULT_KEY)
    assert result.returncode == 0, result.stderr
    assert "published development default" in result.stderr
