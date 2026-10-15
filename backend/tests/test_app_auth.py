"""Android sign-in: system browser → deep link with a one-time code → PKCE-verified token exchange."""

from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app.api import auth as auth_api
from app.auth import google
from app.auth.session import (
    OAUTH_STATE_COOKIE,
    SESSION_COOKIE,
    AppHandoff,
    OAuthState,
    SessionData,
    seal,
    unseal,
)
from tests.test_auth import SECRET, SPREADSHEET_ID, USER, tokens

pytestmark = pytest.mark.usefixtures("fake_storage")

APP_REDIRECT = "com.loonietoonie.app://auth/callback"
VERIFIER, CHALLENGE = google.new_pkce_pair()


def start_app_login(client: TestClient, challenge: str = CHALLENGE) -> dict[str, str]:
    response = client.get("/api/auth/google", params={"app": "android", "challenge": challenge})
    assert response.status_code == 302
    return {key: values[0] for key, values in parse_qs(urlparse(response.headers["location"]).query).items()}


def app_sign_in(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> Any:
    params = start_app_login(client)
    monkeypatch.setattr(google, "exchange_code", lambda settings, code, verifier, redirect_uri: tokens())
    monkeypatch.setattr(google, "verify_id_token", lambda settings, token: USER)
    return client.get("/api/auth/callback", params={"code": "auth-code", "state": params["state"]})


def deep_link_params(response: Any) -> dict[str, str]:
    location = response.headers["location"]
    assert location.startswith(APP_REDIRECT + "?")
    return {key: values[0] for key, values in parse_qs(urlparse(location).query).items()}


def test_app_login_requires_a_valid_challenge(client: TestClient) -> None:
    assert client.get("/api/auth/google", params={"app": "android"}).status_code == 422
    assert client.get("/api/auth/google", params={"app": "android", "challenge": "short"}).status_code == 422
    assert client.get("/api/auth/google", params={"app": "ios", "challenge": CHALLENGE}).status_code == 422


def test_app_login_remembers_the_challenge(client: TestClient) -> None:
    response = client.get("/api/auth/google", params={"app": "android", "challenge": CHALLENGE})

    state = unseal(response.cookies[OAUTH_STATE_COOKIE], OAuthState, SECRET, 60)
    assert state is not None and state.app_challenge == CHALLENGE


def test_web_login_has_no_app_challenge(client: TestClient) -> None:
    response = client.get("/api/auth/google")

    state = unseal(response.cookies[OAUTH_STATE_COOKIE], OAuthState, SECRET, 60)
    assert state is not None and state.app_challenge is None


def test_app_callback_returns_to_the_app_without_a_cookie(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = app_sign_in(client, monkeypatch)

    code = deep_link_params(response)["code"]
    assert SESSION_COOKIE not in response.cookies
    handoff = unseal(code, AppHandoff, SECRET, 60)
    assert handoff is not None
    assert handoff.challenge == CHALLENGE
    assert handoff.session.spreadsheet_id == SPREADSHEET_ID


def test_token_exchange_and_bearer_access(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    code = deep_link_params(app_sign_in(client, monkeypatch))["code"]

    response = client.post("/api/auth/app/token", json={"code": code, "verifier": VERIFIER})

    assert response.status_code == 200
    token = response.json()["token"]
    assert response.json()["expires_in_days"] == 30
    client.cookies.clear()
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == "ann@example.com"


def test_intercepted_code_is_useless_without_the_verifier(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    code = deep_link_params(app_sign_in(client, monkeypatch))["code"]
    other_verifier, _ = google.new_pkce_pair()

    response = client.post("/api/auth/app/token", json={"code": code, "verifier": other_verifier})

    assert response.status_code == 400


def test_expired_code_is_rejected(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    code = deep_link_params(app_sign_in(client, monkeypatch))["code"]
    monkeypatch.setattr(auth_api, "APP_HANDOFF_MAX_AGE_SECONDS", -1)

    assert client.post("/api/auth/app/token", json={"code": code, "verifier": VERIFIER}).status_code == 400


def test_session_token_is_not_accepted_as_a_code(client: TestClient) -> None:
    session = SessionData(sub="u", email="e@example.com", refresh_token="r", spreadsheet_id="s")

    response = client.post("/api/auth/app/token", json={"code": seal(session, SECRET), "verifier": VERIFIER})

    assert response.status_code == 400


def test_app_errors_return_to_the_app(client: TestClient) -> None:
    start_app_login(client)

    response = client.get("/api/auth/callback", params={"error": "access_denied"})

    assert deep_link_params(response) == {"error": "access_denied"}


def test_app_missing_drive_permission_returns_to_the_app(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    params = start_app_login(client)
    monkeypatch.setattr(
        google, "exchange_code", lambda settings, code, verifier, redirect_uri: tokens(scope="openid email")
    )
    monkeypatch.setattr(google, "verify_id_token", lambda settings, token: USER)

    response = client.get("/api/auth/callback", params={"code": "auth-code", "state": params["state"]})

    assert deep_link_params(response) == {"error": "drive_permission_required"}


@pytest.mark.parametrize("header", ["Bearer not-a-token", "Bearer ", "Basic abc", "bearer"])
def test_bad_authorization_headers_are_rejected(client: TestClient, header: str) -> None:
    assert client.get("/api/auth/me", headers={"Authorization": header}).status_code == 401


def test_cors_allows_the_app_webview(client: TestClient) -> None:
    response = client.options(
        "/api/auth/me",
        headers={
            "Origin": "https://localhost",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://localhost"


def test_app_sign_in_can_use_its_own_google_callback(monkeypatch: pytest.MonkeyPatch) -> None:
    """The phone reaches the backend at another address (e.g. Tailscale) than the website."""
    from app.config import get_settings
    from app.main import app
    from tests.test_auth import make_settings

    app_callback = "https://laptop.example.ts.net/api/auth/callback"
    settings = make_settings(app_google_redirect_uri=app_callback)
    app.dependency_overrides[get_settings] = lambda: settings
    used: list[str] = []

    def exchange(settings: object, code: str, verifier: str, redirect_uri: str) -> object:
        used.append(redirect_uri)
        return tokens()

    monkeypatch.setattr(google, "exchange_code", exchange)
    monkeypatch.setattr(google, "verify_id_token", lambda settings, token: USER)
    with TestClient(app, follow_redirects=False) as client:
        app_params = start_app_login(client)
        client.get("/api/auth/callback", params={"code": "c", "state": app_params["state"]})
        web = client.get("/api/auth/google")
    app.dependency_overrides.clear()

    assert app_params["redirect_uri"] == app_callback
    assert used == [app_callback]
    web_redirect = parse_qs(urlparse(web.headers["location"]).query)["redirect_uri"][0]
    assert web_redirect == settings.google_redirect_uri
