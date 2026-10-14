import logging
import secrets
from typing import Annotated, Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from app.auth import google
from app.auth.dependencies import get_current_user
from app.auth.session import (
    APP_HANDOFF_MAX_AGE_SECONDS,
    MIN_SECRET_LENGTH,
    OAUTH_STATE_COOKIE,
    OAUTH_STATE_COOKIE_PATH,
    OAUTH_STATE_MAX_AGE_SECONDS,
    SESSION_COOKIE,
    AppHandoff,
    OAuthState,
    SessionData,
    seal,
    unseal,
)
from app.auth.tokens import get_credentials, remember_access_token
from app.config import Settings, get_settings
from app.storage import setup as storage_setup
from app.storage.repository import StorageError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

SettingsDep = Annotated[Settings, Depends(get_settings)]


SPREADSHEET_URL = "https://docs.google.com/spreadsheets/d/{}"


class UserResponse(BaseModel):
    email: str
    name: str | None
    picture: str | None
    spreadsheet_url: str
    default_currency: str


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


class AppTokenRequest(BaseModel):
    code: str
    verifier: str = Field(min_length=43, max_length=128)


class AppTokenResponse(BaseModel):
    token: str
    expires_in_days: int


# RFC 7636 S256 challenge: 43 base64url characters.
CHALLENGE_PATTERN = r"^[A-Za-z0-9_-]{43}$"


def _redirect_with_error(settings: Settings, reason: str, to_app: bool = False) -> RedirectResponse:
    target = (
        f"{settings.app_redirect_uri}?{urlencode({'error': reason})}"
        if to_app
        else f"{settings.frontend_url}/?{urlencode({'auth_error': reason})}"
    )
    response = RedirectResponse(target, status_code=status.HTTP_302_FOUND)
    response.delete_cookie(OAUTH_STATE_COOKIE, path=OAUTH_STATE_COOKIE_PATH)
    return response


@router.get("/google")
def login(
    settings: SettingsDep,
    app: Literal["android"] | None = None,
    challenge: Annotated[str | None, Query(pattern=CHALLENGE_PATTERN)] = None,
) -> RedirectResponse:
    """Start Google Sign-In: redirect the browser to Google's consent screen.

    The Android app opens this in the system browser with `app=android` and the S256
    `challenge` of a verifier it keeps; the callback then returns to the app (see /app/token).
    """
    _require_auth_config(settings)
    if app is not None and challenge is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail="challenge is required for app sign-in"
        )

    state = secrets.token_urlsafe(32)
    code_verifier, code_challenge = google.new_pkce_pair()

    response = RedirectResponse(
        google.build_authorization_url(settings, state, code_challenge), status_code=status.HTTP_302_FOUND
    )
    response.set_cookie(
        OAUTH_STATE_COOKIE,
        seal(
            OAuthState(state=state, code_verifier=code_verifier, app_challenge=challenge if app else None),
            settings.session_secret,
        ),
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

    saved = unseal(
        request.cookies.get(OAUTH_STATE_COOKIE),
        OAuthState,
        settings.session_secret,
        OAUTH_STATE_MAX_AGE_SECONDS,
    )
    to_app = saved is not None and saved.app_challenge is not None

    def fail(reason: str) -> RedirectResponse:
        return _redirect_with_error(settings, reason, to_app)

    if error:
        return fail("access_denied" if error == "access_denied" else "google_error")
    if saved is None or not code or not state or not secrets.compare_digest(state, saved.state):
        return fail("invalid_state")

    try:
        tokens = google.exchange_code(settings, code, saved.code_verifier)
        user = google.verify_id_token(settings, tokens.id_token)
    except google.GoogleAuthError:
        logger.warning("Google Sign-In failed", exc_info=True)
        return fail("google_error")

    # Google lets users untick individual permissions on the consent screen.
    if google.DRIVE_FILE_SCOPE not in tokens.granted_scopes:
        return fail("drive_permission_required")
    if not tokens.refresh_token:
        logger.warning("Google did not return a refresh token for %s", user.sub)
        return fail("google_error")

    # First sign-in creates the spreadsheet; later sign-ins migrate it to the current schema.
    remember_access_token(tokens.refresh_token, tokens.access_token, tokens.expires_in)
    try:
        credentials = get_credentials(tokens.refresh_token, settings)
        spreadsheet_id = storage_setup.prepare_user_storage(credentials)
    except (google.GoogleAuthError, StorageError):
        logger.warning("Could not prepare storage for %s", user.sub, exc_info=True)
        return fail("storage_error")

    session = SessionData(
        sub=user.sub,
        email=user.email,
        name=user.name,
        picture=user.picture,
        refresh_token=tokens.refresh_token,
        spreadsheet_id=spreadsheet_id,
    )
    if saved.app_challenge is not None:
        # Android: hand the session to the app through its deep link (no cookie for the browser).
        handoff = seal(AppHandoff(session=session, challenge=saved.app_challenge), settings.session_secret)
        response = RedirectResponse(
            f"{settings.app_redirect_uri}?{urlencode({'code': handoff})}", status_code=status.HTTP_302_FOUND
        )
        response.delete_cookie(OAUTH_STATE_COOKIE, path=OAUTH_STATE_COOKIE_PATH)
        return response

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


@router.post("/app/token", response_model=AppTokenResponse)
def app_token(body: AppTokenRequest, settings: SettingsDep) -> AppTokenResponse:
    """Android: exchange the deep-link code plus the app's PKCE verifier for a session token."""
    _require_auth_config(settings)
    handoff = unseal(body.code, AppHandoff, settings.session_secret, APP_HANDOFF_MAX_AGE_SECONDS)
    if handoff is None or not secrets.compare_digest(google.pkce_challenge(body.verifier), handoff.challenge):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="Sign-in expired or invalid. Please try again."
        )
    return AppTokenResponse(
        token=seal(handoff.session, settings.session_secret), expires_in_days=settings.session_max_age_days
    )


@router.get("/me", response_model=UserResponse)
def me(user: Annotated[SessionData, Depends(get_current_user)], settings: SettingsDep) -> UserResponse:
    return UserResponse(
        email=user.email,
        name=user.name,
        picture=user.picture,
        spreadsheet_url=SPREADSHEET_URL.format(user.spreadsheet_id),
        default_currency=settings.default_currency,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(settings: SettingsDep) -> Response:
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(
        SESSION_COOKIE, path="/", secure=settings.cookie_secure, httponly=True, samesite="lax"
    )
    return response
