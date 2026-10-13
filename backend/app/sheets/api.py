"""Thin wrapper over the Google Drive and Sheets REST APIs.

Everything that talks to Google for storage goes through `SheetsApi`, so the
setup logic and the repository can be tested with an in-memory fake.
"""

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from google.auth.credentials import Credentials
from gspread.http_client import HTTPClient

from app.sheets.schema import APP_PROPERTY_KEY, tab_range

DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"
DRIVE_UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"
REQUEST_TIMEOUT_SECONDS = 15


@dataclass
class SheetInfo:
    sheet_id: int
    title: str
    column_count: int
    headers: list[str] = field(default_factory=list)


class SheetsApi(Protocol):
    def find_file_ids(self, marker: str, mime_type: str) -> list[str]:
        """IDs of non-trashed files this app created with the given marker, oldest first."""
        ...

    def create_file(self, name: str, mime_type: str, marker: str, parent_id: str | None) -> str: ...

    def upload_file(self, name: str, media_type: str, data: bytes, parent_id: str, marker: str) -> str:
        """Upload a file into a Drive folder and return its web link."""
        ...

    def get_sheets(self, spreadsheet_id: str) -> list[SheetInfo]:
        """All tabs with their header row (row 1)."""
        ...

    def batch_update(self, spreadsheet_id: str, requests: list[dict[str, Any]]) -> None: ...

    def write_values(self, spreadsheet_id: str, data: list[tuple[str, list[list[str]]]]) -> None:
        """Write several ranges in one call. Values are stored exactly as given (RAW)."""
        ...

    def append_values(self, spreadsheet_id: str, range_: str, rows: list[list[str]]) -> None:
        """Append rows after the last row of the tab in one call. Values are stored as given (RAW)."""
        ...

    def read_values(self, spreadsheet_id: str, range_: str) -> list[list[str]]:
        """Cell values as text. Numbers typed into the sheet come back unformatted (no currency signs)."""
        ...


class GoogleSheetsApi:
    def __init__(self, credentials: Credentials) -> None:
        self._http = HTTPClient(auth=credentials)
        self._http.set_timeout(REQUEST_TIMEOUT_SECONDS)

    def find_file_ids(self, marker: str, mime_type: str) -> list[str]:
        query = (
            f"appProperties has {{ key='{APP_PROPERTY_KEY}' and value='{marker}' }}"
            f" and mimeType='{mime_type}' and trashed=false"
        )
        response = self._http.request(
            "get",
            DRIVE_FILES_URL,
            params={"q": query, "fields": "files(id)", "orderBy": "createdTime", "spaces": "drive"},
        )
        return [file["id"] for file in response.json().get("files", [])]

    def create_file(self, name: str, mime_type: str, marker: str, parent_id: str | None) -> str:
        body: dict[str, Any] = {
            "name": name,
            "mimeType": mime_type,
            "appProperties": {APP_PROPERTY_KEY: marker},
        }
        if parent_id:
            body["parents"] = [parent_id]
        response = self._http.request("post", DRIVE_FILES_URL, params={"fields": "id"}, json=body)
        file_id: str = response.json()["id"]
        return file_id

    def upload_file(self, name: str, media_type: str, data: bytes, parent_id: str, marker: str) -> str:
        metadata = {"name": name, "parents": [parent_id], "appProperties": {APP_PROPERTY_KEY: marker}}
        boundary = f"loonietoonie-{uuid.uuid4().hex}"
        metadata_part = f"Content-Type: application/json; charset=UTF-8\r\n\r\n{json.dumps(metadata)}"
        body = b"".join(
            [
                f"--{boundary}\r\n{metadata_part}\r\n".encode(),
                f"--{boundary}\r\nContent-Type: {media_type}\r\n\r\n".encode(),
                data,
                f"\r\n--{boundary}--\r\n".encode(),
            ]
        )
        response = self._http.request(
            "post",
            DRIVE_UPLOAD_URL,
            params={"uploadType": "multipart", "fields": "id,webViewLink"},
            data=body,
            headers={"Content-Type": f"multipart/related; boundary={boundary}"},
        )
        link: str = response.json()["webViewLink"]
        return link

    def get_sheets(self, spreadsheet_id: str) -> list[SheetInfo]:
        metadata = self._http.fetch_sheet_metadata(
            spreadsheet_id, params={"fields": "sheets(properties(sheetId,title,gridProperties(columnCount)))"}
        )
        sheets = [
            SheetInfo(
                sheet_id=sheet["properties"]["sheetId"],
                title=sheet["properties"]["title"],
                column_count=sheet["properties"].get("gridProperties", {}).get("columnCount", 26),
            )
            for sheet in metadata.get("sheets", [])
        ]
        if sheets:
            ranges = [tab_range(sheet.title, "1:1") for sheet in sheets]
            response = self._http.values_batch_get(spreadsheet_id, ranges)
            for sheet, value_range in zip(sheets, response.get("valueRanges", []), strict=False):
                rows = value_range.get("values", [])
                sheet.headers = [str(cell).strip() for cell in rows[0]] if rows else []
        return sheets

    def batch_update(self, spreadsheet_id: str, requests: list[dict[str, Any]]) -> None:
        self._http.batch_update(spreadsheet_id, {"requests": requests})

    def write_values(self, spreadsheet_id: str, data: list[tuple[str, list[list[str]]]]) -> None:
        self._http.values_batch_update(
            spreadsheet_id,
            {
                "valueInputOption": "RAW",
                "data": [{"range": range_, "values": values} for range_, values in data],
            },
        )

    def append_values(self, spreadsheet_id: str, range_: str, rows: list[list[str]]) -> None:
        self._http.values_append(
            spreadsheet_id,
            range_,
            params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
            body={"values": rows},
        )

    def read_values(self, spreadsheet_id: str, range_: str) -> list[list[str]]:
        response = self._http.values_get(
            spreadsheet_id,
            range_,
            params={"valueRenderOption": "UNFORMATTED_VALUE", "dateTimeRenderOption": "FORMATTED_STRING"},
        )
        return [[_cell_text(cell) for cell in row] for row in response.get("values", [])]


def _cell_text(cell: Any) -> str:
    if isinstance(cell, bool):
        return "TRUE" if cell else "FALSE"
    if isinstance(cell, float) and cell.is_integer():
        return str(int(cell))
    return str(cell)
