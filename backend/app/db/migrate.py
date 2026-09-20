"""Tiny idempotent schema patcher for columns added after a table already
exists on disk. ``Base.metadata.create_all`` only creates missing tables --
it never alters an existing one -- so a from-scratch clone still gets the
new columns from the model definitions above, while a database created
before those columns existed needs this to catch up.
"""

from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


# Columns added to existing tables after they were first created, as
# (table, column, type). ALTER TABLE ... ADD COLUMN with these types is
# accepted by both SQLite and Postgres, which is all this needs to cover.
_ADDED_COLUMNS: list[tuple[str, str, str]] = [
    ("users", "theme", "VARCHAR(16)"),
    # Lets a code be bound to someone who hasn't registered yet.
    ("access_codes", "assigned_email", "VARCHAR(255)"),
    ("users", "accent_profile", "VARCHAR(32)"),
    # Match statistics, backfilled from football-data.co.uk. Nullable, so
    # adding them to a populated table is safe and instant -- existing rows
    # simply have no stats until the enrichment script runs.
    ("matches", "referee", "VARCHAR(64)"),
    ("matches", "home_shots", "INTEGER"),
    ("matches", "away_shots", "INTEGER"),
    ("matches", "home_shots_on_target", "INTEGER"),
    ("matches", "away_shots_on_target", "INTEGER"),
    ("matches", "home_corners", "INTEGER"),
    ("matches", "away_corners", "INTEGER"),
    ("matches", "home_fouls", "INTEGER"),
    ("matches", "away_fouls", "INTEGER"),
    ("matches", "home_yellows", "INTEGER"),
    ("matches", "away_yellows", "INTEGER"),
    ("matches", "home_reds", "INTEGER"),
    ("matches", "away_reds", "INTEGER"),
    # API-Football's own fixture id -- odds capture matches against this,
    # not team names (the /odds response carries no team names at all).
    ("matches", "api_fixture_id", "INTEGER"),
    # Set only by the real live-board sync, each time it confirms a fixture
    # is still in play -- lets "genuinely live" be answered by recency
    # instead of a status column that can get stuck.
    # TIMESTAMP, not DATETIME -- Postgres has no DATETIME type at all, and a
    # missing type on ALTER TABLE fails the whole schema migration, which
    # takes every data endpoint down with it (see main.py's _try_init_database).
    ("matches", "live_synced_at", "TIMESTAMP"),
    # Structured (match_id, market, selection) refs for a best-picks-style
    # answer, so the UI can offer to price them for real via AI Generation.
    ("chat_messages", "picks", "JSON"),
    # True only when the optional LLM rewriter actually replaced this row's
    # content -- see app/assistant/llm.py.
    ("chat_messages", "rewritten", "BOOLEAN"),
    # DEFAULT TRUE, unlike every column above -- every Admin Pick that
    # existed before this column did was a bookmaker-priced AI Generation
    # slip, never the new probability-only kind, so a backfilled NULL would
    # misclassify every one of them as unpriced. TRUE/FALSE is a valid
    # boolean-column default in both SQLite (3.23+) and Postgres.
    ("admin_picks", "priced", "BOOLEAN DEFAULT TRUE"),
    # A booking code the admin typed in by hand after generating it on a
    # real bookmaker's site -- see AdminPick's own docstring.
    ("admin_picks", "booking_code", "VARCHAR(64)"),
    ("admin_picks", "booking_code_bookmaker", "VARCHAR(64)"),
    # "system_weekly_<tier>" on a row scripts/generate_weekly_picks.py
    # created; null on everything an admin built by hand.
    ("admin_picks", "source", "VARCHAR(32)"),
]


def ensure_schema(engine: Engine) -> None:
    """Add columns that model definitions gained after a table already
    existed on disk.

    ``Base.metadata.create_all`` creates missing tables but never alters an
    existing one, so a database created before a column was added needs this
    to catch up. Every column here is nullable, which is what makes applying
    it to a live, populated database safe.
    """

    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())

    statements: list[str] = []
    for table, column, column_type in _ADDED_COLUMNS:
        if table not in table_names:
            # A table that doesn't exist yet will be created complete by
            # create_all -- nothing to patch.
            continue
        if column in {col["name"] for col in inspector.get_columns(table)}:
            continue
        statements.append(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")

    if statements:
        with engine.begin() as conn:
            for statement in statements:
                conn.execute(text(statement))


def _migrate_pending_users_to_active(engine: Engine) -> None:
    """One-time flip for accounts stuck in the old ``pending`` status.

    The pending/superadmin-approve gate was replaced by the access-code
    system: there is nothing left to approve a pending account *into*, since
    every account -- new or old -- now needs to redeem a code to unlock
    features. Idempotent: once no row is ``pending`` this UPDATE matches
    nothing.
    """

    inspector = inspect(engine)
    if "users" not in inspector.get_table_names():
        return
    with engine.begin() as conn:
        conn.execute(text("UPDATE users SET status = 'active' WHERE status = 'pending'"))


def init_db(engine: Engine) -> None:
    """Bring a database up to date: create missing tables, add columns that
    existing tables are missing, and run one-time data migrations.

    These must always run together, and keeping them separate meant six
    scripts called ``create_all`` alone and would crash against any database
    created before the newest column -- including, in one case, the
    production database a nightly job runs against. One call is harder to
    get half right.
    """

    # Imported here rather than at module scope: models imports this module's
    # sibling, and a top-level import would be circular.
    from app.db.models import Base

    Base.metadata.create_all(bind=engine)
    ensure_schema(engine)
    _migrate_pending_users_to_active(engine)
