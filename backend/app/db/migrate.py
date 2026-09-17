"""Tiny idempotent schema patcher for columns added after a table already
exists on disk. ``Base.metadata.create_all`` only creates missing tables --
it never alters an existing one -- so a from-scratch clone still gets the
new columns from the model definitions above, while a database created
before those columns existed needs this to catch up.
"""

from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


def ensure_schema(engine: Engine) -> None:
    inspector = inspect(engine)
    if "users" not in inspector.get_table_names():
        return

    existing_columns = {col["name"] for col in inspector.get_columns("users")}
    statements = []
    if "theme" not in existing_columns:
        statements.append("ALTER TABLE users ADD COLUMN theme VARCHAR(16)")
    if "accent_profile" not in existing_columns:
        statements.append("ALTER TABLE users ADD COLUMN accent_profile VARCHAR(32)")

    if statements:
        with engine.begin() as conn:
            for statement in statements:
                conn.execute(text(statement))
