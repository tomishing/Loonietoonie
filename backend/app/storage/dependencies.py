from typing import Annotated

from fastapi import Depends, HTTPException, status

from app.auth.dependencies import get_current_user
from app.auth.google import GoogleAuthError
from app.auth.session import SessionData
from app.auth.tokens import get_credentials
from app.config import Settings, get_settings
from app.sheets.api import GoogleSheetsApi
from app.storage.repository import Repository
from app.storage.sheets_repository import SheetsRepository


def get_repository(
    user: Annotated[SessionData, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Repository:
    try:
        credentials = get_credentials(user.refresh_token, settings)
    except GoogleAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Google access expired. Please sign in again."
        ) from exc
    return SheetsRepository(GoogleSheetsApi(credentials), user.spreadsheet_id)
