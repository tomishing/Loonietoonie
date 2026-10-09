import logging
import secrets
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.auth import google
from app.auth.dependencies import get_current_user
from app.auth.session import (
    MIN_SECRET_LENGTH,
    OAUTH_STATE_COOKIE,
    OAUTH_STATE_COOKIE_PATH,
    OAUTH_STATE_MAX_AGE_SECONDS,
    SESSION_COOKIE,
    OAuthState,
    SessionData,
    seal,
    unseal,
)
from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

SettingsDep = Annotated[Settings, Depends(get_settings)]


class UserResponse(BaseModel):
    email: str
    name: str | None
    picture: str | None


def _require_auth_config(settings: Settings) -> None:
    if (
        not settings.google_client_id
        or not settings.google_client_secret
        or len(settings.session_secret) < MIN_SECRET_LENGTH
    ):
        logger.error(
            "Google Sign-In needs GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, and a SESSION_SECRET "
            "of at least %d characters.",
            MIN_SECRET_LENGTH,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google Sign-In is not configured on the server.",
        )


def _redirect_with_error(settings: Settings, reason: str) -> RedirectResponse:
    response = RedirectResponse(
        f"{settings.frontend_url}/?{urlencode({'auth_error': reason})}", status_code=status.HTTP_302_FOUND
    )
    response.delete_cookie(OAUTH_STATE_COOKIE, path=OAUTH_STATE_COOKIE_PATH)
    return response


@router.get("/google")
def login(settings: SettingsDep) -> RedirectResponse:
    """Start Google Sign-In: redirect the browser to Google's consent screen."""
    _require_auth_config(settings)

    state = secrets.token_urlsafe(32)
    code_verifier, code_challenge = google.new_pkce_pair()

    response = RedirectResponse(
        google.build_authorization_url(settings, state, code_challenge), status_code=status.HTTP_302_FOUND
    )
    response.set_cookie(
        OAUTH_STATE_COOKIE,
        seal(OAuthState(state=state, code_verifier=code_verifier), settings.session_secret),
        max_age=OAUTH_STATE_MAX_AGE_SECONDS,
        path=OAUTH_STATE_COOKIE_PATH,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )
    return response


@router.get("/callback")
def callback(
    request: Request,
    settings: SettingsDep,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Finish Google Sign-In, set the session cookie, and send the browser back to the app."""
    _require_auth_config(settings)

    if error:
        return _redirect_with_error(settings, "access_denied" if error == "access_denied" else "google_error")

    saved = unseal(
        request.cookies.get(OAUTH_STATE_COOKIE),
        OAuthState,
        settings.session_secret,
        OAUTH_STATE_MAX_AGE_SECONDS,
    )
    if saved is None or not code or not state or not secrets.compare_digest(state, saved.state):
        return _redirect_with_error(settings, "invalid_state")

    try:
        tokens = google.exchange_code(settings, code, saved.code_verifier)
        user = google.verify_id_token(settings, tokens.id_token)
    except google.GoogleAuthError:
        logger.warning("Google Sign-In failed", exc_info=True)
        return _redirect_with_error(settings, "google_error")

    # Google lets users untick individual permissions on the consent screen.
    if google.DRIVE_FILE_SCOPE not in tokens.granted_scopes:
        return _redirect_with_error(settings, "drive_permission_required")
    if not tokens.refresh_token:
        logger.warning("Google did not return a refresh token for %s", user.sub)
        return _redirect_with_error(settings, "google_error")

    session = SessionData(
        sub=user.sub,
        email=user.email,
        name=user.name,
        picture=user.picture,
        refresh_token=tokens.refresh_token,
    )
    response = RedirectResponse(f"{settings.frontend_url}/", status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        SESSION_COOKIE,
        seal(session, settings.session_secret),
        max_age=settings.session_max_age_days * 24 * 60 * 60,
        path="/",
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )
    response.delete_cookie(OAUTH_STATE_COOKIE, path=OAUTH_STATE_COOKIE_PATH)
    return response


@router.get("/me", response_model=UserResponse)
def me(user: Annotated[SessionData, Depends(get_current_user)]) -> UserResponse:
    return UserResponse(email=user.email, name=user.name, picture=user.picture)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(settings: SettingsDep) -> Response:
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(
        SESSION_COOKIE, path="/", secure=settings.cookie_secure, httponly=True, samesite="lax"
    )
    return response
