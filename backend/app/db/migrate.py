"""Tiny idempotent schema patcher for columns added after a table already
exists on disk. ``Base.metadata.create_all`` only creates missing tables --
it never alters an existing one -- so a from-scratch clone still gets the
new columns from the model definitions above, while a database created
before those columns existed needs this to catch up.
"""

from __future__ import annotations

import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


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
    # True only when the optional LLM answered a message the grounded
    # pipeline couldn't match to anything -- see app/assistant/llm.py's
    # answer_general_question().
    ("chat_messages", "general_chat", "BOOLEAN"),
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
    # Per-site booking codes -- see BookingSlip.site_codes.
    ("booking_slips", "site_codes", "JSON"),
    # Collected starting with the under-18 signup gate -- null for every
    # account that registered before it existed, see User.date_of_birth.
    ("users", "date_of_birth", "DATE"),
    # Referral program -- see User.referral_code / referred_by_user_id and
    # _backfill_referral_codes below (the bare ALTER TABLE here can't add
    # the unique index too; that comes after every row has a value).
    ("users", "referral_code", "VARCHAR(16)"),
    ("users", "referred_by_user_id", "INTEGER"),
    # Result tracking -- see app.pick_settlement. DEFAULT 'pending' (unlike
    # most columns above) so every pre-existing row reads the same as a
    # freshly created one rather than NULL, which settlement doesn't treat
    # as a valid state.
    ("admin_picks", "result", "VARCHAR(16) DEFAULT 'pending'"),
    ("admin_picks", "leg_results", "JSON"),
    ("admin_picks", "settled_at", "TIMESTAMP"),
    ("booking_slips", "result", "VARCHAR(16) DEFAULT 'pending'"),
    ("booking_slips", "leg_results", "JSON"),
    ("booking_slips", "settled_at", "TIMESTAMP"),
    ("featured_picks", "probability_at_pick", "FLOAT"),
    ("featured_picks", "result", "VARCHAR(16) DEFAULT 'pending'"),
    ("featured_picks", "settled_at", "TIMESTAMP"),
]


def ensure_schema(engine: Engine) -> None:
    """Add columns that model definitions gained after a table already
    existed on disk.

    ``Base.metadata.create_all`` creates missing tables but never alters an
    existing one, so a database created before a column was added needs this
    to catch up. Every column here is nullable, which is what makes applying
    it to a live, populated database safe.

    Each ADD COLUMN runs in its own transaction and a failure on one is
    logged and skipped rather than raised -- this used to run every
    statement in one transaction, so a single bad one (a column type a
    specific Postgres version rejects, a brief connection drop) rolled back
    every other column too, silently leaving a production database on an
    old schema until the next deploy happened to succeed end to end.
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

    for statement in statements:
        try:
            with engine.begin() as conn:
                conn.execute(text(statement))
        except Exception:
            logger.exception("Schema patch failed, skipping: %s", statement)


def _enable_row_level_security(engine: Engine) -> None:
    """Locks every table's row-level security on, Postgres only.

    This backend is the only thing that ever talks to the database, and it
    always connects as Supabase's ``postgres`` role (see DEPLOYMENT.md's
    pooler connection string) -- a superuser, which bypasses row security
    unconditionally regardless of whether it's enabled. So this changes
    nothing about what the app itself can do.

    What it does close is Supabase's *separate* auto-generated PostgREST/
    GraphQL API, which this project never uses but which exists on every
    Supabase project regardless and talks to the database as the
    anon/authenticated roles -- roles row security actually applies to.
    Supabase's own security advisor flags every public table with RLS
    disabled for exactly this reason (an access code, a user's password
    hash, or a redeemed grant readable by anyone who finds the anon key),
    and the fix needs no policies: RLS enabled with none defined denies
    those roles outright, which is the correct default for a table with no
    legitimate reason to be reachable from anywhere but this backend.

    SQLite (local dev, tests) has no such concept, so this is a no-op there.
    """

    if engine.dialect.name != "postgresql":
        return

    # Imported here rather than at module scope, same reason as init_db's
    # own Base import: models imports this module's sibling, so a
    # top-level import here would be circular.
    from app.db.models import Base

    inspector = inspect(engine)
    existing = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table in Base.metadata.tables:
            if table not in existing:
                continue
            conn.execute(text(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY'))


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


def _make_booking_slips_combined_odds_nullable(engine: Engine) -> None:
    """``combined_odds`` was NOT NULL from when booking_slips was defined for
    a removed booking-code-aggregator flow that always had a price. The
    current direct-to-site flow (routes_betcodes.book_picks) has no price of
    its own to combine -- each site prices its own slip -- so it needs this
    column nullable.

    Postgres can do this in place with a plain ALTER COLUMN; SQLite can't
    without a full table rebuild, so this is a no-op there (every SQLite
    database -- local dev, tests -- is created fresh via create_all from the
    current, already-nullable model definition, so there's nothing to fix).

    Best-effort and deliberately never raises: this is a secondary cleanup,
    never allowed to block ensure_schema's column additions below, which is
    why it only ever runs after them (see init_db) -- an earlier version of
    this function did a DROP+CREATE TABLE *before* ensure_schema and, by
    failing on a production database for reasons that were never fully
    pinned down, silently prevented every column ensure_schema was supposed
    to add, anywhere, for as long as it kept failing.
    """

    if engine.dialect.name != "postgresql":
        return
    try:
        inspector = inspect(engine)
        if "booking_slips" not in inspector.get_table_names():
            return
        columns = {c["name"]: c for c in inspector.get_columns("booking_slips")}
        if columns.get("combined_odds", {}).get("nullable", True):
            return  # already nullable, or column doesn't exist yet (ensure_schema adds it)
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE booking_slips ALTER COLUMN combined_odds DROP NOT NULL"))
    except Exception:
        logger.exception("Could not make booking_slips.combined_odds nullable -- leaving it as-is")


def _backfill_referral_codes(engine: Engine) -> None:
    """Gives every account that predates the referral program its own code,
    then locks in uniqueness with an index -- can't be done in the same
    step as adding the bare column (ensure_schema above), since a freshly
    added column is NULL on every existing row and a unique index over all-
    NULL values would be pointless, not because it would fail (most engines
    treat NULL as distinct from NULL for uniqueness) but because the whole
    point is every account actually having a usable code afterward.

    Idempotent: a row that already has one is left alone, and creating the
    index again once it exists is a no-op (IF NOT EXISTS).
    """

    inspector = inspect(engine)
    if "users" not in inspector.get_table_names():
        return

    # Imported here, not at module scope, for the same reason init_db's own
    # Base import is local: app.access imports app.db.models, which this
    # module's sibling relationship with models.py makes a cycle risk at
    # import time otherwise.
    from app.access import generate_referral_code_string

    with engine.begin() as conn:
        existing_codes = {row[0] for row in conn.execute(text("SELECT referral_code FROM users WHERE referral_code IS NOT NULL"))}
        rows = conn.execute(text("SELECT id FROM users WHERE referral_code IS NULL")).fetchall()
        for (user_id,) in rows:
            for _ in range(5):
                candidate = generate_referral_code_string()
                if candidate not in existing_codes:
                    existing_codes.add(candidate)
                    break
            else:
                continue  # astronomically unlikely; this row waits for the lazy fallback instead
            conn.execute(text("UPDATE users SET referral_code = :code WHERE id = :id"), {"code": candidate, "id": user_id})

        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_referral_code ON users (referral_code)"))


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
    # ensure_schema is the one step every other endpoint depends on being
    # current (it adds every column a model gained after its table already
    # existed) -- it must run unconditionally, before anything that could
    # itself fail, so a secondary cleanup below can never block it again.
    ensure_schema(engine)
    _make_booking_slips_combined_odds_nullable(engine)
    _migrate_pending_users_to_active(engine)
    _backfill_referral_codes(engine)
    _enable_row_level_security(engine)
