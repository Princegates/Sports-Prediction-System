from collections.abc import Iterator
from urllib.parse import urlparse

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

settings = get_settings()

database_url = settings.normalized_database_url
is_sqlite = database_url.startswith("sqlite")

# Ports that mean "a connection pooler in transaction mode is in front of
# Postgres". Supabase's Supavisor uses 6543 for transaction mode (5432 is
# session mode, which does support prepared statements); pgbouncer's own
# convention is 6432. Both multiplex one server connection across many
# clients, handing a different backend to each transaction.
TRANSACTION_POOLER_PORTS = {6543, 6432}


def uses_transaction_pooler(url: str) -> bool:
    try:
        return urlparse(url).port in TRANSACTION_POOLER_PORTS
    except ValueError:
        # Malformed port -- scripts/check_database_url.py reports that
        # properly; here it just means "assume not pooled".
        return False


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

    if uses_transaction_pooler(database_url):
        # A transaction-mode pooler gives each transaction whichever backend
        # is free, so a statement prepared on one is absent from the next.
        # psycopg3 prepares automatically once a query has run five times
        # (prepare_threshold=5), which makes this fail *late* and look
        # random: the app works, then the queries it runs most often start
        # raising `prepared statement "_pg3_0" already exists` while rarer
        # ones keep working. None disables automatic preparation entirely.
        #
        # The cost is re-planning each statement, which is small here --
        # this workload is a few hundred queries per refresh, not a
        # high-throughput OLTP service -- and it only applies when actually
        # connecting through such a pooler.
        engine_kwargs["connect_args"] = {"prepare_threshold": None}

engine = create_engine(database_url, **engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
