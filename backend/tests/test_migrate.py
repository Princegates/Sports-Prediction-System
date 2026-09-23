"""Row-level security: every public table gets RLS enabled on Postgres, so
Supabase's own auto-generated PostgREST/GraphQL API -- which this project
never uses, but which exists on every Supabase project regardless and talks
to the database as the anon/authenticated roles -- can't read or write any
of it. This backend always connects as the ``postgres`` superuser, which
bypasses row security unconditionally, so enabling it changes nothing about
what the app itself can do; it only closes an API path the app never opens.
"""

from __future__ import annotations

from app.db import migrate
from app.db.models import Base
from app.db.session import engine


def test_enable_row_level_security_is_a_noop_on_sqlite(db_session):
    """Local dev and this whole test suite run on SQLite, which has no
    concept of row security -- this must never try to run Postgres-only
    syntax against it."""

    migrate._enable_row_level_security(engine)  # must not raise


def test_enable_row_level_security_locks_every_table_on_postgres(monkeypatch, db_session):
    captured: list[str] = []

    class FakeConn:
        def execute(self, stmt) -> None:
            captured.append(str(stmt))

    class FakeBeginCtx:
        def __enter__(self):
            return FakeConn()

        def __exit__(self, *exc) -> bool:
            return False

    # Reverted before this function returns, not just at test teardown --
    # the db_session fixture's own teardown (drop_all) needs the engine's
    # real .begin() back, and fixture teardown order would otherwise run
    # that before monkeypatch's own revert.
    with monkeypatch.context() as m:
        m.setattr(engine.dialect, "name", "postgresql", raising=False)
        m.setattr(engine, "begin", lambda: FakeBeginCtx())
        migrate._enable_row_level_security(engine)

    locked_tables = {stmt.split('"')[1] for stmt in captured}
    assert locked_tables == set(Base.metadata.tables)
    assert all("ENABLE ROW LEVEL SECURITY" in stmt for stmt in captured)


def test_init_db_runs_end_to_end_on_sqlite_without_error():
    """init_db is what actually runs on every app startup (see
    app.main._try_init_database) -- this is the same call, same order,
    against the same kind of database the test suite already uses."""

    migrate.init_db(engine)
    Base.metadata.drop_all(bind=engine)
