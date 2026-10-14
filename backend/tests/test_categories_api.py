from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.auth.session import SESSION_COOKIE, SessionData, seal
from app.config import get_settings
from app.main import app
from app.storage.dependencies import get_repository
from app.storage.models import Category
from app.storage.repository import StorageError
from tests.test_auth import SECRET, make_settings


class FakeRepository:
    def __init__(self, categories: list[Category] | None = None, fail: bool = False) -> None:
        self._categories = categories or []
        self._fail = fail

    def list_categories(self) -> list[Category]:
        if self._fail:
            raise StorageError("Sheets API down")
        return self._categories


@pytest.fixture
def client() -> Iterator[TestClient]:
    app.dependency_overrides[get_settings] = lambda: make_settings()
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def signed_in(client: TestClient) -> TestClient:
    session = SessionData(
        sub="user-1", email="ann@example.com", refresh_token="refresh-1", spreadsheet_id="s1"
    )
    client.cookies.set(SESSION_COOKIE, seal(session, SECRET))
    return client


def test_lists_categories(client: TestClient) -> None:
    app.dependency_overrides[get_repository] = lambda: FakeRepository([Category(name="Groceries", icon="🛒")])

    response = client.get("/api/categories")

    assert response.status_code == 200
    assert response.json() == [{"name": "Groceries", "icon": "🛒", "subcategories": []}]


def test_requires_sign_in(client: TestClient) -> None:
    assert client.get("/api/categories").status_code == 401


def test_storage_failure_returns_502(client: TestClient) -> None:
    app.dependency_overrides[get_repository] = lambda: FakeRepository(fail=True)

    response = client.get("/api/categories")

    assert response.status_code == 502
    assert "spreadsheet" in response.json()["detail"]


def test_expired_google_access_returns_401(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.auth.google import GoogleAuthError
    from app.storage import dependencies

    def revoked(refresh_token: str, settings: object) -> None:
        raise GoogleAuthError("invalid_grant")

    monkeypatch.setattr(dependencies, "get_credentials", revoked)

    response = signed_in(client).get("/api/categories")

    assert response.status_code == 401
