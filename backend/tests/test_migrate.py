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


def _fake_pg_connection(monkeypatch, *, already_enabled: set[str] = frozenset()):
    """Fakes just enough of a Postgres connection for
    _enable_row_level_security: the lookup query returns ``already_enabled``
    as (tablename,) rows, and every other statement (the ALTER TABLEs) is
    just captured. Returns the list ALTER TABLE statements land in."""

    captured: list[str] = []

    class FakeConn:
        def execute(self, stmt):
            text = str(stmt)
            if "FROM pg_tables" in text:
                return [(name,) for name in already_enabled]
            captured.append(text)
            return None

    class FakeBeginCtx:
        def __enter__(self):
            return FakeConn()

        def __exit__(self, *exc) -> bool:
            return False

    # Reverted before this function returns, not just at test teardown --
    # the db_session fixture's own teardown (drop_all) needs the engine's
    # real .begin() back, and fixture teardown order would otherwise run
    # that before monkeypatch's own revert.
    monkeypatch.setattr(engine.dialect, "name", "postgresql", raising=False)
    monkeypatch.setattr(engine, "begin", lambda: FakeBeginCtx())
    return captured


def test_enable_row_level_security_locks_every_table_on_postgres(monkeypatch, db_session):
    with monkeypatch.context() as m:
        captured = _fake_pg_connection(m)
        migrate._enable_row_level_security(engine)

    locked_tables = {stmt.split('"')[1] for stmt in captured}
    assert locked_tables == set(Base.metadata.tables)
    assert all("ENABLE ROW LEVEL SECURITY" in stmt for stmt in captured)


def test_enable_row_level_security_skips_tables_already_done(monkeypatch, db_session):
    """Regression test: this used to re-run ALTER TABLE ... ENABLE ROW
    LEVEL SECURITY for every table on every single process start (plus
    every 30s self-heal retry in /api/health while the database stays
    unreachable) regardless of whether it had already been set -- each
    statement needs a brief ACCESS EXCLUSIVE lock to grant, and that
    sequential sweep over the whole schema, every boot, is exactly what
    pushed one real deploy's startup past Render's 5-minute port-scan
    window against a slow database. A table pg_tables already reports as
    rowsecurity=true must be left alone."""

    already = set(Base.metadata.tables) - {"teams"}
    with monkeypatch.context() as m:
        captured = _fake_pg_connection(m, already_enabled=already)
        migrate._enable_row_level_security(engine)

    locked_tables = {stmt.split('"')[1] for stmt in captured}
    assert locked_tables == {"teams"}


def test_enable_row_level_security_does_nothing_when_every_table_is_already_done(monkeypatch, db_session):
    with monkeypatch.context() as m:
        captured = _fake_pg_connection(m, already_enabled=set(Base.metadata.tables))
        migrate._enable_row_level_security(engine)

    assert captured == []


def test_init_db_runs_end_to_end_on_sqlite_without_error():
    """init_db is what actually runs on every app startup (see
    app.main._try_init_database) -- this is the same call, same order,
    against the same kind of database the test suite already uses."""

    migrate.init_db(engine)
    Base.metadata.drop_all(bind=engine)


def test_a_broken_schema_patch_never_blocks_the_others(monkeypatch, db_session):
    """Regression test: ensure_schema used to run every ADD COLUMN in one
    transaction, so a single failing statement rolled every other column
    back too. In production this combined with a *different* bug (a
    now-removed migration step that could raise before ensure_schema ever
    ran) to leave admin_picks, booking_slips and featured_picks missing
    their result-tracking columns entirely -- every endpoint touching them
    failed with psycopg2.errors.UndefinedColumn until a deploy happened to
    get through cleanly. Each statement must now run in its own transaction
    and a failure must only skip that one column."""

    from sqlalchemy import text as sa_text

    original_add_columns = migrate._ADDED_COLUMNS
    monkeypatch.setattr(
        migrate,
        "_ADDED_COLUMNS",
        [
            ("users", "_test_col_a", "VARCHAR(16)"),
            ("users", "_test_col_b", "THIS IS NOT A VALID TYPE("),  # deliberately broken
            ("users", "_test_col_c", "VARCHAR(16)"),
        ],
    )
    try:
        migrate.ensure_schema(engine)  # must not raise
    finally:
        monkeypatch.setattr(migrate, "_ADDED_COLUMNS", original_add_columns)

    with engine.connect() as conn:
        columns = {row[1] for row in conn.execute(sa_text("PRAGMA table_info(users)"))}
    assert "_test_col_a" in columns
    assert "_test_col_c" in columns
    assert "_test_col_b" not in columns


def test_booking_slips_combined_odds_nullable_fix_is_a_noop_on_sqlite(db_session):
    migrate._make_booking_slips_combined_odds_nullable(engine)  # must not raise


def test_booking_slips_combined_odds_nullable_fix_runs_on_postgres(monkeypatch, db_session):
    captured: list[str] = []

    class FakeConn:
        def execute(self, stmt) -> None:
            captured.append(str(stmt))

    class FakeBeginCtx:
        def __enter__(self):
            return FakeConn()

        def __exit__(self, *exc) -> bool:
            return False

    class FakeInspector:
        def get_table_names(self):
            return ["booking_slips"]

        def get_columns(self, table):
            return [{"name": "combined_odds", "nullable": False}]

    with monkeypatch.context() as m:
        m.setattr(engine.dialect, "name", "postgresql", raising=False)
        m.setattr(engine, "begin", lambda: FakeBeginCtx())
        m.setattr(migrate, "inspect", lambda eng: FakeInspector())
        migrate._make_booking_slips_combined_odds_nullable(engine)

    assert len(captured) == 1
    assert "ALTER TABLE booking_slips ALTER COLUMN combined_odds DROP NOT NULL" in captured[0]


def test_booking_slips_combined_odds_nullable_fix_never_raises(monkeypatch, db_session):
    """Defense in depth: whatever goes wrong here must never stop
    ensure_schema's own columns (run before this, per init_db) from having
    already been applied -- see the regression test above for why that
    ordering matters."""

    def boom(eng):
        raise RuntimeError("simulated failure")

    with monkeypatch.context() as m:
        m.setattr(engine.dialect, "name", "postgresql", raising=False)
        m.setattr(migrate, "inspect", boom)
        migrate._make_booking_slips_combined_odds_nullable(engine)  # must not raise


def test_no_added_column_uses_a_datetime_type_postgres_does_not_have():
    """Regression test for the bug that actually broke production here:
    "DATETIME" is a SQLite/MySQL type name, not a real Postgres one (Postgres
    only has TIMESTAMP). SQLite's lenient type-affinity rules accept it
    silently, so this is invisible to the whole rest of this test suite --
    three ADD COLUMN statements used it, each one failed against the real
    production database, and -- thanks to ensure_schema's one-statement-at-
    a-time transactions (see the test above) -- only those three columns
    were ever missing, but every endpoint touching them broke until this was
    caught by hand reading Render's logs. A plain string search is a cheap,
    permanent guard against writing "DATETIME" in this list again."""

    offending = [(table, column) for table, column, column_type in migrate._ADDED_COLUMNS if "DATETIME" in column_type.upper()]
    assert offending == [], f"Use TIMESTAMP, not DATETIME (Postgres has no such type): {offending}"
