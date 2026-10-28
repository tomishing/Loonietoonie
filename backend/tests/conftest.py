"""Fixtures shared by the sign-in tests (test_auth.py, test_app_auth.py) and the routes that call
Claude (signed_in)."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from google.oauth2.credentials import Credentials

from app.auth.dependencies import get_current_user
from app.auth.session import SessionData
from app.config import get_settings
from app.main import app
from app.services.usage import UsageLimiter, get_usage_limiter
from app.storage import setup as storage_setup


@pytest.fixture
def client() -> Iterator[TestClient]:
    """A test client using the sign-in test settings; redirects are not followed."""
    from tests.test_auth import make_settings

    app.dependency_overrides[get_settings] = lambda: make_settings()
    with TestClient(app, follow_redirects=False) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def fake_storage(monkeypatch: pytest.MonkeyPatch) -> list[Credentials]:
    """Replace spreadsheet setup during sign-in; records the credentials it was called with."""
    from tests.test_auth import SPREADSHEET_ID

    calls: list[Credentials] = []

    def prepare(credentials: Credentials) -> str:
        calls.append(credentials)
        return SPREADSHEET_ID

    monkeypatch.setattr(storage_setup, "prepare_user_storage", prepare)
    return calls


@pytest.fixture
def signed_in() -> Iterator[UsageLimiter]:
    """Sign in a test user, with a fresh Claude usage limiter (app/services/usage.py)."""
    limiter = UsageLimiter()
    user = SessionData(sub="user-1", email="t@example.com", refresh_token="r", spreadsheet_id="s")
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_usage_limiter] = lambda: limiter
    yield limiter
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_usage_limiter, None)
