"""Unit tests for app.ai_generation_quota -- the daily cap (renewed at midnight) on AI
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


def test_the_allowance_renews_in_full_at_midnight(db_session):
    """Runs used yesterday -- even a minute before midnight -- don't count
    today; runs used since midnight do."""

    user = _make_user(db_session, "member3@example.com", override=2)
    now = dt.datetime(2026, 11, 19, 9, 30)
    db_session.add_all([
        AIGenerationLog(user_id=user.id, created_at=dt.datetime(2026, 11, 18, 23, 59)),
        AIGenerationLog(user_id=user.id, created_at=dt.datetime(2026, 11, 18, 12, 0)),
    ])
    db_session.commit()
    assert ai_generation_quota.used_today(db_session, user.id, now) == 0
    assert ai_generation_quota.quota_remaining(db_session, user, now) == 2

    db_session.add(AIGenerationLog(user_id=user.id, created_at=dt.datetime(2026, 11, 19, 0, 0)))
    db_session.commit()
    assert ai_generation_quota.used_today(db_session, user.id, now) == 1
    assert ai_generation_quota.quota_remaining(db_session, user, now) == 1


def test_a_run_just_before_midnight_still_counts_until_midnight(db_session):
    user = _make_user(db_session, "late@example.com", override=1)
    db_session.add(AIGenerationLog(user_id=user.id, created_at=dt.datetime(2026, 11, 18, 23, 58)))
    db_session.commit()
    assert ai_generation_quota.quota_remaining(db_session, user, dt.datetime(2026, 11, 18, 23, 59)) == 0
    assert ai_generation_quota.quota_remaining(db_session, user, dt.datetime(2026, 11, 19, 0, 0)) == 1


def test_quota_is_per_user_not_shared(db_session):
    a = _make_user(db_session, "a@example.com", override=1)
    b = _make_user(db_session, "b@example.com", override=1)
    ai_generation_quota.record_generation(db_session, a)
    assert ai_generation_quota.has_quota_left(db_session, a) is False
    assert ai_generation_quota.has_quota_left(db_session, b) is True


def test_next_free_at_is_the_coming_midnight(db_session):
    user = _make_user(db_session, "waiting@example.com", override=2)
    now = dt.datetime(2026, 11, 19, 15, 0)
    db_session.add_all([
        AIGenerationLog(user_id=user.id, created_at=dt.datetime(2026, 11, 19, 8, 0)),
        AIGenerationLog(user_id=user.id, created_at=dt.datetime(2026, 11, 19, 13, 0)),
    ])
    db_session.commit()

    assert ai_generation_quota.next_free_at(db_session, user, now) == dt.datetime(2026, 11, 20, 0, 0)


def test_next_free_at_is_none_while_runs_remain_or_unlimited(db_session):
    member = _make_user(db_session, "plenty@example.com", override=5)
    ai_generation_quota.record_generation(db_session, member)
    admin = _make_user(db_session, "boss@example.com", role="superadmin")

    assert ai_generation_quota.next_free_at(db_session, member) is None
    assert ai_generation_quota.next_free_at(db_session, admin) is None


def test_a_lowered_limit_still_renews_at_midnight(db_session):
    """Three runs counted but the limit is now 1: nothing frees up before
    midnight, and the full (new) allowance is back after it."""

    user = _make_user(db_session, "lowered@example.com", override=1)
    now = dt.datetime(2026, 11, 19, 20, 0)
    db_session.add_all([
        AIGenerationLog(user_id=user.id, created_at=dt.datetime(2026, 11, 19, h, 0)) for h in (2, 10, 19)
    ])
    db_session.commit()

    assert ai_generation_quota.quota_remaining(db_session, user, now) == 0
    assert ai_generation_quota.next_free_at(db_session, user, now) == dt.datetime(2026, 11, 20, 0, 0)
    assert ai_generation_quota.quota_remaining(db_session, user, dt.datetime(2026, 11, 20, 0, 1)) == 1


def test_a_run_is_taken_before_the_engine_and_only_once_for_the_last_slot(db_session):
    # Two requests racing for the last run: the one written first gets it.
    from app.db.models import User

    app_settings.set_values(db_session, {"ai_generation_daily_limit": 1})
    user = User(email="race@example.com", name="R", password_hash="x", role="user", status="active")
    db_session.add(user)
    db_session.commit()

    first = ai_generation_quota.reserve(db_session, user)
    second = ai_generation_quota.reserve(db_session, user)

    assert first is not None and second is None
    assert ai_generation_quota.quota_remaining(db_session, user) == 0


def test_past_the_allowance_a_reserved_run_spends_one_credit_and_release_gives_it_back(db_session):
    from app.db.models import User

    user = User(email="credit@example.com", name="C", password_hash="x", role="user", status="active",
                ai_generation_credits=1, daily_ai_generation_limit_override=0)
    db_session.add(user)
    db_session.commit()

    taken = ai_generation_quota.reserve(db_session, user)
    assert taken == ai_generation_quota.CREDIT
    assert db_session.get(User, user.id).ai_generation_credits == 0
    assert ai_generation_quota.reserve(db_session, user) is None

    ai_generation_quota.release(db_session, user, taken)
    assert db_session.get(User, user.id).ai_generation_credits == 1
