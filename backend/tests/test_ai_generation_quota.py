"""Unit tests for app.ai_generation_quota -- the rolling-24-hour cap on AI
Generation calls, admin-configurable globally with a per-user override for
members who pay for more.
"""

from __future__ import annotations

import datetime as dt

from app import ai_generation_quota, app_settings
from app.auth.passwords import hash_password
from app.db.models import AIGenerationLog, User
from app.timeutil import utcnow


def _make_user(db, email: str, role: str = "user", override: int | None = None) -> User:
    user = User(
        email=email, name=email.split("@")[0], password_hash=hash_password("a-good-password"),
        role=role, status="active", daily_ai_generation_limit_override=override,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_default_limit_comes_from_the_global_setting(db_session):
    user = _make_user(db_session, "member@example.com")
    app_settings.set_values(db_session, {"ai_generation_daily_limit": 5})
    assert ai_generation_quota.daily_limit(db_session, user) == 5


def test_a_per_user_override_wins_over_the_global_setting(db_session):
    user = _make_user(db_session, "payer@example.com", override=100)
    app_settings.set_values(db_session, {"ai_generation_daily_limit": 5})
    assert ai_generation_quota.daily_limit(db_session, user) == 100


def test_a_zero_override_is_a_real_zero_not_unlimited(db_session):
    user = _make_user(db_session, "blocked@example.com", override=0)
    assert ai_generation_quota.daily_limit(db_session, user) == 0
    assert ai_generation_quota.has_quota_left(db_session, user) is False


def test_superadmin_is_never_limited_even_with_an_override_set(db_session):
    admin = _make_user(db_session, "admin@example.com", role="superadmin", override=0)
    assert ai_generation_quota.daily_limit(db_session, admin) is None
    assert ai_generation_quota.quota_remaining(db_session, admin) is None
    assert ai_generation_quota.has_quota_left(db_session, admin) is True


def test_recording_a_generation_counts_against_the_limit(db_session):
    user = _make_user(db_session, "member2@example.com", override=3)
    assert ai_generation_quota.quota_remaining(db_session, user) == 3

    ai_generation_quota.record_generation(db_session, user)
    ai_generation_quota.record_generation(db_session, user)
    assert ai_generation_quota.quota_remaining(db_session, user) == 1
    assert ai_generation_quota.has_quota_left(db_session, user) is True

    ai_generation_quota.record_generation(db_session, user)
    assert ai_generation_quota.quota_remaining(db_session, user) == 0
    assert ai_generation_quota.has_quota_left(db_session, user) is False


def test_the_window_is_rolling_not_a_calendar_day(db_session):
    """A call from 25 hours ago has already rolled off -- this isn't a
    midnight reset, it's a trailing 24 hours."""

    user = _make_user(db_session, "member3@example.com", override=1)
    old = utcnow() - dt.timedelta(hours=25)
    db_session.add(AIGenerationLog(user_id=user.id, created_at=old))
    db_session.commit()

    assert ai_generation_quota.used_today(db_session, user.id) == 0
    assert ai_generation_quota.has_quota_left(db_session, user) is True

    recent = utcnow() - dt.timedelta(hours=1)
    db_session.add(AIGenerationLog(user_id=user.id, created_at=recent))
    db_session.commit()
    assert ai_generation_quota.used_today(db_session, user.id) == 1
    assert ai_generation_quota.has_quota_left(db_session, user) is False


def test_quota_is_per_user_not_shared(db_session):
    a = _make_user(db_session, "a@example.com", override=1)
    b = _make_user(db_session, "b@example.com", override=1)
    ai_generation_quota.record_generation(db_session, a)
    assert ai_generation_quota.has_quota_left(db_session, a) is False
    assert ai_generation_quota.has_quota_left(db_session, b) is True
