from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from app.auth.session import SESSION_COOKIE, SessionData, unseal
from app.config import Settings, get_settings


def is_app_request(request: Request) -> bool:
    """The request comes from the Android app (bearer token), not the web app (cookie)."""
    return _bearer_token(request) is not None


def _bearer_token(request: Request) -> str | None:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None


def get_current_user(request: Request, settings: Annotated[Settings, Depends(get_settings)]) -> SessionData:
    """Return the signed-in user, or respond 401.

    The web app sends the session cookie; the Android app sends the same sealed session
    as a bearer token (its WebView can't use the backend's cookies). A session whose email
    was removed from ALLOWED_EMAILS also gets 401, so the app goes back to sign-in.
    """
    session = unseal(
        _bearer_token(request) or request.cookies.get(SESSION_COOKIE),
        SessionData,
        settings.session_secret,
        settings.session_max_age_days * 24 * 60 * 60,
    )
    if session is None or not settings.sign_in_allowed_for(session.email):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not signed in")
    return session
