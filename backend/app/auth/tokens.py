"""Short-lived Google access tokens, cached in server memory.

The session cookie holds only the refresh token. Access tokens last about an hour,
so each one is kept in RAM until shortly before it expires. Nothing is written
to disk; after a restart each user just needs one extra refresh.
"""

import hashlib
import threading
from datetime import UTC, datetime, timedelta

from google.auth.exceptions import RefreshError, TransportError
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials

from app.auth.google import TOKEN_ENDPOINT, GoogleAuthError
from app.config import Settings

EXPIRY_MARGIN = timedelta(minutes=2)
MAX_ENTRIES = 10_000


def _utcnow() -> datetime:
    # google-auth uses naive UTC datetimes for Credentials.expiry.
    return datetime.now(UTC).replace(tzinfo=None)


class AccessTokenCache:
    def __init__(self, max_entries: int = MAX_ENTRIES) -> None:
        self._entries: dict[str, tuple[str, datetime]] = {}
        self._lock = threading.Lock()
        self._max_entries = max_entries

    @staticmethod
    def _key(refresh_token: str) -> str:
        return hashlib.sha256(refresh_token.encode("utf-8")).hexdigest()

    def get(self, refresh_token: str) -> tuple[str, datetime] | None:
        with self._lock:
            entry = self._entries.get(self._key(refresh_token))
        if entry is None or entry[1] - EXPIRY_MARGIN <= _utcnow():
            return None
        return entry

    def put(self, refresh_token: str, access_token: str, expiry: datetime) -> None:
        with self._lock:
            if len(self._entries) >= self._max_entries:
                now = _utcnow()
                self._entries = {key: value for key, value in self._entries.items() if value[1] > now}
                if len(self._entries) >= self._max_entries:
                    self._entries.pop(next(iter(self._entries)))
            self._entries[self._key(refresh_token)] = (access_token, expiry)


access_tokens = AccessTokenCache()


def remember_access_token(refresh_token: str, access_token: str, expires_in: int) -> None:
    access_tokens.put(refresh_token, access_token, _utcnow() + timedelta(seconds=expires_in))


def get_credentials(refresh_token: str, settings: Settings) -> Credentials:
    """Google credentials with a valid access token, refreshing it if needed."""
    credentials = Credentials(  # type: ignore[no-untyped-call]
        token=None,
        refresh_token=refresh_token,
        token_uri=TOKEN_ENDPOINT,
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
    )
    cached = access_tokens.get(refresh_token)
    if cached is not None:
        credentials.token, credentials.expiry = cached
        return credentials

    try:
        credentials.refresh(GoogleRequest())  # type: ignore[no-untyped-call]
    except (RefreshError, TransportError) as exc:
        raise GoogleAuthError("Could not refresh the Google access token") from exc

    if credentials.token and credentials.expiry:
        access_tokens.put(refresh_token, credentials.token, credentials.expiry)
    return credentials
