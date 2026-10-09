from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from app.auth.session import SESSION_COOKIE, SessionData, unseal
from app.config import Settings, get_settings


def get_current_user(request: Request, settings: Annotated[Settings, Depends(get_settings)]) -> SessionData:
    """Return the signed-in user, or respond 401."""
    session = unseal(
        request.cookies.get(SESSION_COOKIE),
        SessionData,
        settings.session_secret,
        settings.session_max_age_days * 24 * 60 * 60,
    )
    if session is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not signed in")
    return session
