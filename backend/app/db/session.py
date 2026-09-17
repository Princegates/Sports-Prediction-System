from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

settings = get_settings()

database_url = settings.normalized_database_url
is_sqlite = database_url.startswith("sqlite")

engine_kwargs: dict = {}
if is_sqlite:
    # SQLite's default thread check rejects the connection being reused
    # across FastAPI's threadpool workers.
    engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    # Free-tier Postgres (Neon, Supabase and friends) either scales to zero
    # or aggressively drops idle connections, so a pooled connection is
    # frequently dead by the time the next request reuses it. Without
    # pre-ping the first request after any quiet period fails with
    # "server closed the connection unexpectedly" -- which on a free tier
    # means most first requests. Recycling below the typical idle timeout
    # keeps the pool from holding connections the server has already gone.
    engine_kwargs["pool_pre_ping"] = True
    engine_kwargs["pool_recycle"] = 280

engine = create_engine(database_url, **engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
