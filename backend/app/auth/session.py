"""Encrypted cookies for the sign-in state and the user session.

There is no server-side session store: the session, including the Google refresh
token, lives in an HttpOnly cookie encrypted with SESSION_SECRET (Fernet).
"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, ValidationError

SESSION_COOKIE = "lt_session"
OAUTH_STATE_COOKIE = "lt_oauth"
OAUTH_STATE_COOKIE_PATH = "/api/auth"
OAUTH_STATE_MAX_AGE_SECONDS = 10 * 60

MIN_SECRET_LENGTH = 32


class OAuthState(BaseModel):
    """Kept in a short-lived cookie between /api/auth/google and /api/auth/callback."""

    state: str
    code_verifier: str
    # Set when the sign-in was started by the Android app (S256 challenge of the app's verifier).
    app_challenge: str | None = None


class SessionData(BaseModel):
    sub: str
    email: str
    name: str | None = None
    picture: str | None = None
    refresh_token: str
    # Sessions from before phase 3 lack this field, fail validation, and must sign in again.
    spreadsheet_id: str


APP_HANDOFF_MAX_AGE_SECONDS = 2 * 60


class AppHandoff(BaseModel):
    """One-time code passed from the browser to the Android app through a deep link.

    Only redeemable with the verifier matching `challenge`, which never leaves the app,
    so another app that intercepts the deep link can't use it.
    """

    session: SessionData
    challenge: str


def _fernet(secret: str) -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def seal(data: BaseModel, secret: str) -> str:
    return _fernet(secret).encrypt(data.model_dump_json().encode("utf-8")).decode("ascii")


def unseal[T: BaseModel](token: str | None, model: type[T], secret: str, max_age_seconds: int) -> T | None:
    """Decrypt a cookie value. Returns None if it is missing, tampered with, or too old."""
    if not token:
        return None
    try:
        payload = _fernet(secret).decrypt(token.encode("ascii"), ttl=max_age_seconds)
        return model.model_validate_json(payload)
    except (InvalidToken, ValidationError, UnicodeEncodeError):
        return None
