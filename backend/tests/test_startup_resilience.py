"""The API must start, and stay diagnosable, when the database is down.

Written after a live deployment showed "Failed service" with no further
explanation. ``init_db(engine)`` ran at import time, so a database that was
asleep, over quota or simply slow took the whole process down before it could
serve anything -- including the health check that would have said why. A
free-tier Postgres that scales to zero produces exactly that situation.

The second test covers the symptom that wastes the most time. An unhandled
exception is caught above the CORS middleware, so its 500 carries no
``Access-Control-Allow-Origin`` header, and the browser reports a CORS
failure. You then go and check your origins -- which are fine, because the
database was the problem. The API must answer with a response that still
passes back through the CORS middleware.

These run in a subprocess: the app reads DATABASE_URL at import, and conftest
has already pointed it at a working SQLite file for every other test.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]

# Nothing listens here. Stands in for a database refusing connections.
DEAD_DB = "postgresql://nobody@127.0.0.1:5599/nothing"
ORIGIN = "https://example.pages.dev"


def _run(body: str) -> str:
    script = textwrap.dedent(
        f"""
        import os, sys
        sys.path.insert(0, {str(BACKEND)!r})
        os.environ["DATABASE_URL"] = {DEAD_DB!r}
        os.environ["SECRET_KEY"] = "test-secret"
        os.environ["CORS_ALLOW_ORIGINS"] = {ORIGIN!r}
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app, raise_server_exceptions=False)
        {textwrap.indent(textwrap.dedent(body), "        ").strip()}
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=180
    )
    assert result.returncode == 0, (
        "the app failed to start with an unreachable database:\n" + result.stderr[-3000:]
    )
    return result.stdout


def test_api_starts_and_reports_status_when_the_database_is_unreachable():
    out = _run(
        """
        r = client.get("/api/health")
        print("STATUS", r.status_code)
        print("BODY", r.json())
        """
    )
    assert "STATUS 200" in out, out
    # Liveness stays ok -- reporting unhealthy would have the host kill a
    # process that is about to recover.
    assert "'status': 'ok'" in out, out
    # ...but it must not claim the database is fine.
    assert "'database': 'unavailable'" in out, out


def test_database_errors_keep_their_cors_headers():
    out = _run(
        f"""
        r = client.get("/api/public/stats", headers={{"Origin": {ORIGIN!r}}})
        print("STATUS", r.status_code)
        print("CORS", r.headers.get("access-control-allow-origin"))
        """
    )
    # 503, not 500: the service is up, its dependency isn't.
    assert "STATUS 503" in out, out
    assert f"CORS {ORIGIN}" in out, (
        "a database failure lost its CORS header, so the browser will report this "
        "as a CORS problem and send you to check your origins\n" + out
    )
