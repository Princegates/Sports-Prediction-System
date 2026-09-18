import datetime as dt
import os
import secrets
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_tmp_dir = tempfile.mkdtemp(prefix="sports_prediction_tests_")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp_dir}/test.db"

import pytest

from app.api import rate_limit
from app.auth.passwords import hash_password
from app.auth.tokens import create_token
from app.config import get_settings
from app.db.models import AccessCode, AccessGrant, Base, User
from app.db.session import SessionLocal, engine


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """The rate limiter keeps its counters in module-level state, and every
    test hits the API from the same synthetic client address. Without this,
    the 5-registrations-per-hour limit is shared across the whole suite and
    whichever test happens to run sixth starts failing with a 429 -- a
    failure that disappears when that test is run on its own, which is the
    worst kind to debug. Tests that want to assert the limiter *does* fire
    call it deliberately instead."""

    rate_limit.reset()
    yield
    rate_limit.reset()


@pytest.fixture()
def db_session():
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def grant_active_access(db_session, user: User, days: int = 30) -> AccessGrant:
    """Gives ``user`` a live AccessGrant directly, bypassing the redemption
    endpoint -- for fixtures and tests that just need *some* user with
    working access rather than exercising redemption itself (that flow has
    its own dedicated tests in test_access_codes.py)."""

    code = AccessCode(
        code=f"TEST-{user.id}-{secrets.token_hex(3).upper()}",
        duration_days=days,
        redemption_limit=1,
        redemption_count=1,
        created_by_user_id=user.id,
    )
    db_session.add(code)
    db_session.commit()
    db_session.refresh(code)

    grant = AccessGrant(user_id=user.id, access_code_id=code.id, expires_at=dt.datetime.utcnow() + dt.timedelta(days=days))
    db_session.add(grant)
    db_session.commit()
    db_session.refresh(grant)
    return grant


@pytest.fixture()
def auth_headers(db_session):
    """An active user with a live access grant, plus a valid Authorization
    header for it -- for tests that hit endpoints protected by
    get_current_user and/or require_active_access. Bypasses the
    register -> redeem-a-code flow (that flow has its own dedicated tests)
    since most tests just need *some* fully-set-up logged-in user."""

    user = User(
        email="test-user@example.com",
        name="Test User",
        password_hash=hash_password("test-password-123"),
        role="user",
        status="active",
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    grant_active_access(db_session, user)

    settings = get_settings()
    token = create_token({"user_id": user.id, "role": user.role}, settings.secret_key, settings.session_ttl_seconds)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def user_no_access(db_session):
    """A logged-in, active account with no access grant -- for tests that
    exercise the locked state (get_current_user succeeds, require_active_access
    doesn't)."""

    user = User(
        email="no-access@example.com",
        name="No Access",
        password_hash=hash_password("test-password-123"),
        role="user",
        status="active",
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture()
def headers_no_access(user_no_access):
    settings = get_settings()
    token = create_token(
        {"user_id": user_no_access.id, "role": user_no_access.role}, settings.secret_key, settings.session_ttl_seconds
    )
    return {"Authorization": f"Bearer {token}"}
