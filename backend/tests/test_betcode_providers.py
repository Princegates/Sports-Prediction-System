"""The aggregator boundary. The one thing this must never do -- fabricate a
code that looks real -- is the thing every test here is ultimately checking
for, one way or another.
"""

from __future__ import annotations

import datetime as dt
import json
import urllib.error

import pytest

from app.betcode.providers import (
    BookingCodeError,
    MyBetCodeProvider,
    NotConfiguredProvider,
    ProviderNotConfigured,
    get_provider,
)
from app.betcode.selection import Leg

LEG = Leg(
    match_id=1, league="English Premier League", home_team="Arsenal", away_team="Chelsea",
    kickoff=dt.datetime(2026, 10, 1, 19, 0), market="Match Result", selection="Home Win",
    model_probability=0.7, decimal_odds=1.5,
)


def test_the_default_provider_never_invents_a_code():
    with pytest.raises(ProviderNotConfigured):
        NotConfiguredProvider().create_slip(bookmaker="Bet9ja", legs=[LEG])


def test_get_provider_defaults_to_not_configured(db_session):
    assert isinstance(get_provider(db_session), NotConfiguredProvider)


def test_get_provider_returns_mybetcode_once_configured(db_session):
    from app.db.models import AppSetting

    db_session.add(AppSetting(key="betcode_provider", value="mybetcode", updated_at=dt.datetime.utcnow()))
    db_session.add(AppSetting(key="betcode_api_key", value="test-key-123", updated_at=dt.datetime.utcnow()))
    db_session.commit()

    provider = get_provider(db_session)
    assert isinstance(provider, MyBetCodeProvider)


def test_mybetcode_refuses_to_construct_with_an_empty_key():
    with pytest.raises(ProviderNotConfigured):
        MyBetCodeProvider(api_key="", base_url="https://api.mybetcode.com")


def test_mybetcode_refuses_an_empty_slip():
    provider = MyBetCodeProvider(api_key="k", base_url="https://api.mybetcode.com")
    with pytest.raises(BookingCodeError):
        provider.create_slip(bookmaker="Bet9ja", legs=[])


def test_mybetcode_sends_the_bookmaker_and_every_leg(monkeypatch):
    """Verifies the shape of the payload this client constructs, without a
    real network call -- the endpoint contract itself is unverified (see the
    module docstring); this just proves the client sends what it says it
    sends."""

    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"code": "REAL-CODE-1", "deep_link": "https://bet9ja.com/slip/1"}).encode()

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["body"] = json.loads(request.data.decode())
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    provider = MyBetCodeProvider(api_key="secret-key", base_url="https://api.mybetcode.com")
    result = provider.create_slip(bookmaker="Bet9ja", legs=[LEG])

    assert result.code == "REAL-CODE-1"
    assert result.deep_link == "https://bet9ja.com/slip/1"
    assert captured["body"]["bookmaker"] == "Bet9ja"
    assert len(captured["body"]["selections"]) == 1
    assert captured["body"]["selections"][0]["selection"] == "Home Win"
    assert "secret-key" in captured["headers"].get("Authorization", "")


def test_mybetcode_raises_cleanly_on_an_http_error(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 401, "unauthorized", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    provider = MyBetCodeProvider(api_key="bad-key", base_url="https://api.mybetcode.com")
    with pytest.raises(BookingCodeError, match="401"):
        provider.create_slip(bookmaker="Bet9ja", legs=[LEG])


def test_mybetcode_raises_cleanly_when_no_code_comes_back(monkeypatch):
    """The aggregator answering 200 with no code is treated as a failure, not
    as success with an empty string -- the caller must never store that as
    though it were a real, redeemable code."""

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"status": "pending"}).encode()

    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout=None: FakeResponse())

    provider = MyBetCodeProvider(api_key="k", base_url="https://api.mybetcode.com")
    with pytest.raises(BookingCodeError, match="did not return a code"):
        provider.create_slip(bookmaker="Bet9ja", legs=[LEG])
