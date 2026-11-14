"""scripts/create_superadmin.py only bootstraps: once a superadmin exists it
must not promote or reactivate anyone unless forced."""

import sys
from pathlib import Path

import pytest
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import create_superadmin  # noqa: E402

from app.auth.passwords import hash_password
from app.db.models import User


def _run(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["create_superadmin.py", *args])
    monkeypatch.setenv("SUPERADMIN_PASSWORD", "a-good-password")
    create_superadmin.main()


def _user(db, email, role="user", status="active"):
    u = User(email=email, name=email, password_hash=hash_password("x-password"), role=role, status=status)
    db.add(u)
    db.commit()
    return u


def test_creates_the_first_superadmin(db_session, monkeypatch):
    _run(monkeypatch, "--email", "first@example.com")
    db_session.expire_all()
    user = db_session.execute(select(User).where(User.email == "first@example.com")).scalar_one()
    assert user.role == "superadmin"


def test_does_nothing_once_a_superadmin_exists(db_session, monkeypatch):
    _user(db_session, "owner@example.com", role="superadmin")
    demoted = _user(db_session, "old-admin@example.com", role="user", status="suspended")

    _run(monkeypatch, "--email", "old-admin@example.com")
    db_session.expire_all()
    assert (demoted.role, demoted.status) == ("user", "suspended")

    _run(monkeypatch, "--email", "new@example.com")
    assert db_session.execute(select(User).where(User.email == "new@example.com")).scalar_one_or_none() is None


def test_force_promotes_by_hand(db_session, monkeypatch):
    _user(db_session, "owner@example.com", role="superadmin")
    other = _user(db_session, "helper@example.com")
    _run(monkeypatch, "--email", "helper@example.com", "--force")
    db_session.expire_all()
    assert other.role == "superadmin"
