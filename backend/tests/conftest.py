import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_tmp_dir = tempfile.mkdtemp(prefix="sports_prediction_tests_")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp_dir}/test.db"

import pytest

from app.auth.passwords import hash_password
from app.auth.tokens import create_token
from app.config import get_settings
from app.db.models import Base, User
from app.db.session import SessionLocal, engine


@pytest.fixture()
def db_session():
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def auth_headers(db_session):
    """An active user + a valid Authorization header for it, for tests that
    hit endpoints protected by get_current_user. Bypasses the register ->
    admin-approve flow (that flow has its own dedicated tests) since most
    tests just need *some* logged-in user."""

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

    settings = get_settings()
    token = create_token({"user_id": user.id, "role": user.role}, settings.secret_key, settings.session_ttl_seconds)
    return {"Authorization": f"Bearer {token}"}
