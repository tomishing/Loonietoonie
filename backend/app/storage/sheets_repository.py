import threading
import time

import requests
from google.auth.exceptions import GoogleAuthError as GoogleLibraryAuthError
from gspread.exceptions import APIError

from app.sheets.api import SheetsApi
from app.sheets.schema import TRUE, tab_range
from app.storage.models import Category
from app.storage.repository import StorageError

Record = dict[str, str]

READ_CACHE_TTL_SECONDS = 60.0
READ_CACHE_MAX_ENTRIES = 2_000


class TabReadCache:
    """Recent tab reads per spreadsheet, kept in server memory to save Sheets read quota.

    Edits made directly in the spreadsheet show up after at most the TTL.
    """

    def __init__(self, ttl_seconds: float = READ_CACHE_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._entries: dict[tuple[str, str], tuple[float, list[Record]]] = {}
        self._lock = threading.Lock()

    def get(self, spreadsheet_id: str, tab: str) -> list[Record] | None:
        with self._lock:
            entry = self._entries.get((spreadsheet_id, tab))
        if entry is None or time.monotonic() - entry[0] > self._ttl:
            return None
        return entry[1]

    def put(self, spreadsheet_id: str, tab: str, records: list[Record]) -> None:
        with self._lock:
            if len(self._entries) >= READ_CACHE_MAX_ENTRIES:
                self._entries.clear()
            self._entries[(spreadsheet_id, tab)] = (time.monotonic(), records)

    def invalidate(self, spreadsheet_id: str, tab: str) -> None:
        with self._lock:
            self._entries.pop((spreadsheet_id, tab), None)


tab_read_cache = TabReadCache()


class SheetsRepository:
    def __init__(self, api: SheetsApi, spreadsheet_id: str, cache: TabReadCache = tab_read_cache) -> None:
        self._api = api
        self._spreadsheet_id = spreadsheet_id
        self._cache = cache

    def list_categories(self) -> list[Category]:
        return [
            Category(name=record["name"].strip(), icon=record.get("icon", "").strip())
            for record in self._read_active("categories")
            if record.get("name", "").strip()
        ]

    def _read_active(self, tab: str) -> list[Record]:
        return [record for record in self._read(tab) if record.get("deleted", "").strip().upper() != TRUE]

    def _read(self, tab: str) -> list[Record]:
        """All data rows of a tab as dicts keyed by header, so column order doesn't matter."""
        cached = self._cache.get(self._spreadsheet_id, tab)
        if cached is not None:
            return cached

        try:
            rows = self._api.read_values(self._spreadsheet_id, tab_range(tab))
        except (APIError, requests.RequestException, GoogleLibraryAuthError) as exc:
            raise StorageError(f"Could not read the {tab} tab") from exc

        headers = [header.strip() for header in rows[0]] if rows else []
        records = [
            {
                header: (row[index] if index < len(row) else "")
                for index, header in enumerate(headers)
                if header
            }
            for row in rows[1:]
            if any(cell.strip() for cell in row)
        ]
        self._cache.put(self._spreadsheet_id, tab, records)
        return records
