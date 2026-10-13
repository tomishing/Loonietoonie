import requests
from google.auth.exceptions import GoogleAuthError as GoogleLibraryAuthError
from google.oauth2.credentials import Credentials
from gspread.exceptions import APIError

from app.sheets.api import GoogleSheetsApi
from app.sheets.schema import utc_now_iso
from app.sheets.setup import ensure_spreadsheet
from app.storage.repository import StorageError


def prepare_user_storage(credentials: Credentials) -> str:
    """Create or migrate the user's LoonieToonie spreadsheet. Returns its ID. Runs on every sign-in."""
    try:
        return ensure_spreadsheet(GoogleSheetsApi(credentials), utc_now_iso())
    except (APIError, requests.RequestException, GoogleLibraryAuthError) as exc:
        raise StorageError("Could not set up the LoonieToonie spreadsheet") from exc
