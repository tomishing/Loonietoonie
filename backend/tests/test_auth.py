import base64
import hashlib
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app.auth import google
from app.auth.session import OAUTH_STATE_COOKIE, SESSION_COOKIE, SessionData, seal, unseal
from app.config import Settings, get_settings
from app.main import app

SECRET = "s" * 40
FRONTEND_URL = "http://localhost:5173"


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "google_client_id": "client-123.apps.googleusercontent.com",
        "google_client_secret": "client-secret",
        "google_redirect_uri": "http://testserver/api/auth/callback",
        "session_secret": SECRET,
        "frontend_url": FRONTEND_URL,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


@pytest.fixture
def client() -> Iterator[TestClient]:
    app.dependency_overrides[get_settings] = lambda: make_settings()
    with TestClient(app, follow_redirects=False) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def tokens(
    scope: str = "openid email profile " + google.DRIVE_FILE_SCOPE, refresh_token: str | None = "refresh-1"
) -> google.GoogleTokens:
    return google.GoogleTokens(
        access_token="access-1",
        expires_in=3600,
        id_token="id-token",
        scope=scope,
        refresh_token=refresh_token,
    )


USER = google.GoogleUser(
    sub="user-1", email="ann@example.com", name="Ann", picture="https://example.com/a.png"
)


def start_login(client: TestClient) -> dict[str, str]:
    response = client.get("/api/auth/google")
    assert response.status_code == 302
    return {key: values[0] for key, values in parse_qs(urlparse(response.headers["location"]).query).items()}


def sign_in(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, token_response: google.GoogleTokens | None = None
) -> Any:
    params = start_login(client)
    monkeypatch.setattr(google, "exchange_code", lambda settings, code, verifier: token_response or tokens())
    monkeypatch.setattr(google, "verify_id_token", lambda settings, token: USER)
    return client.get("/api/auth/callback", params={"code": "auth-code", "state": params["state"]})


def test_login_redirects_to_google_with_drive_file_scope_only(client: TestClient) -> None:
    response = client.get("/api/auth/google")

    location = urlparse(response.headers["location"])
    params = parse_qs(location.query)
    assert f"{location.scheme}://{location.netloc}{location.path}" == google.AUTHORIZATION_ENDPOINT
    assert set(params["scope"][0].split()) == {"openid", "email", "profile", google.DRIVE_FILE_SCOPE}
    assert params["code_challenge_method"] == ["S256"]
    assert params["access_type"] == ["offline"]
    assert params["redirect_uri"] == ["http://testserver/api/auth/callback"]
    assert OAUTH_STATE_COOKIE in response.cookies


def test_login_is_unavailable_without_config(client: TestClient) -> None:
    app.dependency_overrides[get_settings] = lambda: make_settings(google_client_secret="")

    response = client.get("/api/auth/google")

    assert response.status_code == 503


def test_login_rejects_short_session_secret(client: TestClient) -> None:
    app.dependency_overrides[get_settings] = lambda: make_settings(session_secret="too-short")

    assert client.get("/api/auth/google").status_code == 503


def test_callback_sends_pkce_verifier_matching_challenge(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    params = start_login(client)
    seen: dict[str, str] = {}

    def fake_exchange(settings: Settings, code: str, code_verifier: str) -> google.GoogleTokens:
        seen["code"] = code
        seen["verifier"] = code_verifier
        return tokens()

    monkeypatch.setattr(google, "exchange_code", fake_exchange)
    monkeypatch.setattr(google, "verify_id_token", lambda settings, token: USER)
    client.get("/api/auth/callback", params={"code": "auth-code", "state": params["state"]})

    digest = hashlib.sha256(seen["verifier"].encode()).digest()
    assert base64.urlsafe_b64encode(digest).rstrip(b"=").decode() == params["code_challenge"]
    assert seen["code"] == "auth-code"


def test_callback_success_sets_session_and_redirects_home(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = sign_in(client, monkeypatch)

    assert response.status_code == 302
    assert response.headers["location"] == f"{FRONTEND_URL}/"
    session = unseal(response.cookies[SESSION_COOKIE], SessionData, SECRET, 60)
    assert session is not None
    assert session.refresh_token == "refresh-1"
    assert session.email == "ann@example.com"
    set_cookie = response.headers.get_list("set-cookie")
    session_cookie = next(c for c in set_cookie if c.startswith(f"{SESSION_COOKIE}="))
    assert "HttpOnly" in session_cookie
    assert "SameSite=lax" in session_cookie


def test_me_returns_user_without_tokens(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    sign_in(client, monkeypatch)

    response = client.get("/api/auth/me")

    assert response.status_code == 200
    assert response.json() == {
        "email": "ann@example.com",
        "name": "Ann",
        "picture": "https://example.com/a.png",
    }


def test_callback_rejects_wrong_state(client: TestClient) -> None:
    start_login(client)

    response = client.get("/api/auth/callback", params={"code": "auth-code", "state": "forged"})

    assert response.headers["location"] == f"{FRONTEND_URL}/?auth_error=invalid_state"
    assert SESSION_COOKIE not in response.cookies


def test_callback_rejects_missing_state_cookie(client: TestClient) -> None:
    response = client.get("/api/auth/callback", params={"code": "auth-code", "state": "anything"})

    assert response.headers["location"] == f"{FRONTEND_URL}/?auth_error=invalid_state"


def test_callback_when_user_cancels(client: TestClient) -> None:
    start_login(client)

    response = client.get("/api/auth/callback", params={"error": "access_denied"})

    assert response.headers["location"] == f"{FRONTEND_URL}/?auth_error=access_denied"


def test_callback_requires_drive_file_permission(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    response = sign_in(client, monkeypatch, tokens(scope="openid email profile"))

    assert response.headers["location"] == f"{FRONTEND_URL}/?auth_error=drive_permission_required"
    assert SESSION_COOKIE not in response.cookies


def test_callback_requires_refresh_token(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    response = sign_in(client, monkeypatch, tokens(refresh_token=None))

    assert response.headers["location"] == f"{FRONTEND_URL}/?auth_error=google_error"


def test_callback_handles_google_failure(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    params = start_login(client)

    def failing_exchange(settings: Settings, code: str, code_verifier: str) -> google.GoogleTokens:
        raise google.GoogleAuthError("invalid_grant")

    monkeypatch.setattr(google, "exchange_code", failing_exchange)
    response = client.get("/api/auth/callback", params={"code": "auth-code", "state": params["state"]})

    assert response.headers["location"] == f"{FRONTEND_URL}/?auth_error=google_error"


def test_me_requires_session(client: TestClient) -> None:
    assert client.get("/api/auth/me").status_code == 401


def test_me_rejects_tampered_session(client: TestClient) -> None:
    client.cookies.set(SESSION_COOKIE, "not-a-valid-token")

    assert client.get("/api/auth/me").status_code == 401


def test_me_rejects_session_sealed_with_other_secret(client: TestClient) -> None:
    session = SessionData(sub="user-1", email="ann@example.com", refresh_token="refresh-1")
    client.cookies.set(SESSION_COOKIE, seal(session, "x" * 40))

    assert client.get("/api/auth/me").status_code == 401


def test_logout_clears_session(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    sign_in(client, monkeypatch)

    response = client.post("/api/auth/logout")

    assert response.status_code == 204
    assert client.get("/api/auth/me").status_code == 401


def test_verify_id_token_rejects_unverified_email(monkeypatch: pytest.MonkeyPatch) -> None:
    claims = {"sub": "user-1", "email": "ann@example.com", "email_verified": False}
    monkeypatch.setattr(
        google.google_id_token, "verify_oauth2_token", lambda token, request, audience: claims
    )

    with pytest.raises(google.GoogleAuthError):
        google.verify_id_token(make_settings(), "id-token")


def test_verify_id_token_wraps_invalid_token(monkeypatch: pytest.MonkeyPatch) -> None:
    def invalid(token: str, request: object, audience: str) -> dict[str, Any]:
        raise ValueError("Wrong audience")

    monkeypatch.setattr(google.google_id_token, "verify_oauth2_token", invalid)

    with pytest.raises(google.GoogleAuthError):
        google.verify_id_token(make_settings(), "id-token")
