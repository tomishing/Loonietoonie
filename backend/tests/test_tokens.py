from datetime import timedelta
from typing import Any

import pytest
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials

from app.auth import tokens
from app.auth.google import GoogleAuthError
from app.auth.tokens import AccessTokenCache, get_credentials, remember_access_token
from app.config import Settings


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch: pytest.MonkeyPatch) -> AccessTokenCache:
    cache = AccessTokenCache()
    monkeypatch.setattr(tokens, "access_tokens", cache)
    return cache


def settings() -> Settings:
    return Settings(_env_file=None, google_client_id="client", google_client_secret="secret")  # type: ignore[call-arg]


def fake_refresh(monkeypatch: pytest.MonkeyPatch, calls: list[str], token: str = "fresh-token") -> None:
    def refresh(self: Credentials, request: Any) -> None:
        calls.append(self.refresh_token or "")
        self.token = token
        self.expiry = tokens._utcnow() + timedelta(hours=1)

    monkeypatch.setattr(Credentials, "refresh", refresh)


def test_uses_cached_token_without_refreshing(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    fake_refresh(monkeypatch, calls)
    remember_access_token("refresh-1", "cached-token", expires_in=3600)

    credentials = get_credentials("refresh-1", settings())

    assert credentials.token == "cached-token"
    assert calls == []


def test_refreshes_once_then_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    fake_refresh(monkeypatch, calls)

    first = get_credentials("refresh-1", settings())
    second = get_credentials("refresh-1", settings())

    assert first.token == second.token == "fresh-token"
    assert calls == ["refresh-1"]


def test_refreshes_token_close_to_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    fake_refresh(monkeypatch, calls)
    remember_access_token("refresh-1", "old-token", expires_in=60)

    credentials = get_credentials("refresh-1", settings())

    assert credentials.token == "fresh-token"
    assert calls == ["refresh-1"]


def test_tokens_are_kept_per_refresh_token(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    fake_refresh(monkeypatch, calls)
    remember_access_token("refresh-1", "token-1", expires_in=3600)

    get_credentials("refresh-2", settings())

    assert calls == ["refresh-2"]


def test_revoked_refresh_token_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def refresh(self: Credentials, request: Any) -> None:
        raise RefreshError("invalid_grant")

    monkeypatch.setattr(Credentials, "refresh", refresh)

    with pytest.raises(GoogleAuthError):
        get_credentials("revoked", settings())


def test_cache_does_not_grow_past_limit() -> None:
    cache = AccessTokenCache(max_entries=2)
    expiry = tokens._utcnow() + timedelta(hours=1)

    for i in range(5):
        cache.put(f"refresh-{i}", f"token-{i}", expiry)

    assert cache.get("refresh-4") is not None
    assert len(cache._entries) <= 2
