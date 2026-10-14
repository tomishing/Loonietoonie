from typing import Annotated

from fastapi import Depends, HTTPException, status

from app.auth.dependencies import get_current_user
from app.auth.google import GoogleAuthError
from app.auth.session import SessionData
from app.auth.tokens import get_credentials
from app.config import Settings, get_settings
from app.sheets.api import GoogleSheetsApi
from app.sheets.schema import utc_now_iso
from app.sheets.setup import migrate_once
from app.storage.repository import Repository, StorageError
from app.storage.sheets_repository import GOOGLE_ERRORS, SheetsRepository


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
    api = GoogleSheetsApi(credentials)
    try:
        migrate_once(api, user.spreadsheet_id, utc_now_iso())
    except GOOGLE_ERRORS as exc:
        raise StorageError("Could not update the LoonieToonie spreadsheet") from exc
    return SheetsRepository(api, user.spreadsheet_id)
