"""Connecting through a transaction-mode pooler must disable psycopg's
automatic prepared statements.

This is the failure that waits a while before appearing. psycopg3 prepares a
statement only after its fifth execution, and a transaction pooler hands each
transaction a different backend, so the prepared statement is missing on the
next one. The result is an app that works on deploy and then starts raising
`prepared statement "_pg3_0" already exists` on its *most common* queries
while rarer ones keep working -- which reads like a load problem rather than
a configuration one.

Supabase's pooler is on 6543 for transaction mode and 5432 for session mode,
and session mode keeps prepared statements working, so the port is the thing
that decides this.
"""

from __future__ import annotations

import pytest

from app.db.session import uses_transaction_pooler

SUPABASE_TRANSACTION = "postgresql+psycopg://postgres.abcdefghijklm:pw@aws-0-eu-central-1.pooler.supabase.com:6543/postgres"
SUPABASE_SESSION = "postgresql+psycopg://postgres.abcdefghijklm:pw@aws-0-eu-central-1.pooler.supabase.com:5432/postgres"
SUPABASE_DIRECT = "postgresql+psycopg://postgres:pw@db.abcdefghijklm.supabase.co:5432/postgres"
NEON_DIRECT = "postgresql+psycopg://user:pw@ep-cool-name-123456.eu-central-1.aws.neon.tech/neondb?sslmode=require"


@pytest.mark.parametrize(
    "url, expected",
    [
        (SUPABASE_TRANSACTION, True),
        # pgbouncer's own conventional transaction-mode port.
        ("postgresql+psycopg://user:pw@host.example.com:6432/db", True),
        (SUPABASE_SESSION, False),
        (SUPABASE_DIRECT, False),
        # No port at all -- Neon's string omits it, and urlparse returns None.
        (NEON_DIRECT, False),
        ("sqlite:///./sports_prediction.db", False),
    ],
)
def test_transaction_pooler_detected_by_port(url: str, expected: bool):
    assert uses_transaction_pooler(url) is expected


def test_a_malformed_port_does_not_raise():
    """check_database_url.py is what explains a bad URL; this must not blow
    up first with a ValueError from urlparse."""

    assert uses_transaction_pooler("postgresql+psycopg://user:pw@host:not-a-port/db") is False
