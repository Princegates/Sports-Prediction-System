"""The preflight that names a malformed DATABASE_URL.

A bad connection string used to surface as a SQLAlchemy traceback ending in
"Could not parse SQLAlchemy URL from given URL string" -- true, and useless.
Several different paste accidents produce that identical message, and telling
them apart cost a deploy each.

Two properties matter here. It must reject what is genuinely broken, and it
must never print the secret: these messages go into CI logs.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_database_url.py"
GOOD = "postgresql://postgres.abc:Foo%40Bar123@aws-0-eu-central-1.pooler.supabase.com:5432/postgres"
SECRET = "Foo%40Bar123"


def _run(value: str | None) -> subprocess.CompletedProcess:
    env = {"PATH": "/usr/bin:/bin", "HOME": "/tmp"}
    if value is not None:
        env["DATABASE_URL"] = value
    return subprocess.run(
        [sys.executable, str(SCRIPT)], capture_output=True, text=True, env=env, timeout=120
    )


def test_a_good_url_passes_and_reports_its_shape():
    r = _run(GOOD)
    assert r.returncode == 0, r.stderr
    assert "aws-0-eu-central-1.pooler.supabase.com" in r.stdout
    assert "postgres.abc" in r.stdout


def test_the_password_is_never_printed():
    """These messages land in CI logs."""

    r = _run(GOOD)
    assert SECRET not in r.stdout + r.stderr
    assert "Foo" not in r.stdout + r.stderr


def test_missing_url_is_reported_clearly():
    r = _run(None)
    assert r.returncode == 1
    assert "not set" in r.stderr


def test_unencoded_at_sign_is_caught_even_though_it_parses():
    """The nastiest case: it parses fine, but the host silently absorbs part
    of the password, so the error you eventually see names a hostname you
    never typed."""

    r = _run("postgresql://postgres.abc:Foo@Bar@aws-0-eu-central-1.pooler.supabase.com:5432/postgres")
    assert r.returncode == 1
    assert "%40" in r.stderr


def test_each_paste_accident_is_named():
    cases = [
        (f'psql "{GOOD}"', "psql"),
        (f'"{GOOD}"', "quotes"),
        (f"DATABASE_URL={GOOD}", "variable name"),
        (GOOD.replace("postgresql://", ""), "scheme"),
        (GOOD.replace("Foo%40Bar123", "[YOUR-PASSWORD]"), "placeholder"),
    ]
    for value, expected in cases:
        r = _run(value)
        assert r.returncode == 1, f"{expected!r} should have been rejected: {value[:40]}"
        assert expected in r.stderr.lower(), (
            f"the message for {expected!r} did not mention it:\n{r.stderr}"
        )
