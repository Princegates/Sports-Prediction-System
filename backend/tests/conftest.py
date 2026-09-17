import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_tmp_dir = tempfile.mkdtemp(prefix="sports_prediction_tests_")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp_dir}/test.db"

import pytest

from app.db.models import Base
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
