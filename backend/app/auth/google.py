"""Google OAuth 2.0 authorization-code flow with PKCE."""

import base64
import hashlib
import secrets
from urllib.parse import urlencode

import requests
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token as google_id_token
from pydantic import BaseModel, ValidationError

from app.config import Settings

AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"

DRIVE_FILE_SCOPE = "https://www.googleapis.com/auth/drive.file"
# Sign-in scopes plus drive.file only. Never add broader Drive or Sheets scopes.
SCOPES = ("openid", "email", "profile", DRIVE_FILE_SCOPE)

REQUEST_TIMEOUT_SECONDS = 10


class GoogleAuthError(Exception):
    """Google rejected the sign-in or returned something unusable."""


class GoogleTokens(BaseModel):
    access_token: str
    expires_in: int
    id_token: str
    scope: str
    refresh_token: str | None = None

    @property
    def granted_scopes(self) -> set[str]:
        return set(self.scope.split())


class GoogleUser(BaseModel):
    sub: str
    email: str
    name: str | None = None
    picture: str | None = None


def new_pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for the S256 PKCE method."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def build_authorization_url(settings: Settings, state: str, code_challenge: str) -> str:
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        # offline + consent: Google returns a refresh token on every sign-in.
        "access_type": "offline",
        "prompt": "consent",
    }
    return f"{AUTHORIZATION_ENDPOINT}?{urlencode(params)}"


def exchange_code(settings: Settings, code: str, code_verifier: str) -> GoogleTokens:
    try:
        response = requests.post(
            TOKEN_ENDPOINT,
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": settings.google_redirect_uri,
                "grant_type": "authorization_code",
                "code_verifier": code_verifier,
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise GoogleAuthError("Could not reach Google's token endpoint") from exc

    if not response.ok:
        error = response.json().get("error", "unknown") if response.content else "unknown"
        raise GoogleAuthError(f"Token exchange failed ({response.status_code}: {error})")

    try:
        return GoogleTokens.model_validate(response.json())
    except (ValueError, ValidationError) as exc:
        raise GoogleAuthError("Unexpected token response from Google") from exc


def verify_id_token(settings: Settings, token: str) -> GoogleUser:
    """Check the ID token's signature, audience, issuer, and expiry, and return the user."""
    try:
        claims = google_id_token.verify_oauth2_token(  # type: ignore[no-untyped-call]
            token, GoogleRequest(), settings.google_client_id
        )
    except ValueError as exc:
        raise GoogleAuthError("Invalid ID token") from exc

    if not claims.get("email_verified"):
        raise GoogleAuthError("Google account email is not verified")

    return GoogleUser(
        sub=claims["sub"],
        email=claims["email"],
        name=claims.get("name"),
        picture=claims.get("picture"),
    )
