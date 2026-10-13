"""In-memory stand-in for Google Drive + Sheets, implementing app.sheets.api.SheetsApi."""

from dataclasses import dataclass, field
from typing import Any

from gspread.utils import a1_to_rowcol

from app.sheets.api import SheetInfo
from app.sheets.schema import SPREADSHEET_MIME_TYPE


@dataclass
class FakeFile:
    name: str
    mime_type: str
    marker: str
    parent_id: str | None
    trashed: bool = False


@dataclass
class FakeSheet:
    sheet_id: int
    title: str
    column_count: int = 26
    frozen_rows: int = 0
    rows: list[list[str]] = field(default_factory=list)


@dataclass
class FakeSheetsApi:
    files: dict[str, FakeFile] = field(default_factory=dict)
    spreadsheets: dict[str, list[FakeSheet]] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)
    starter_sheet_title: str = "Sheet1"
    uploads: dict[str, bytes] = field(default_factory=dict)

    # --- helpers for tests -------------------------------------------------

    def add_spreadsheet(self, file_id: str, sheets: list[FakeSheet], marker: str = "spreadsheet") -> None:
        self.files[file_id] = FakeFile("LoonieToonie", SPREADSHEET_MIME_TYPE, marker, None)
        self.spreadsheets[file_id] = sheets

    def sheet(self, spreadsheet_id: str, title: str) -> FakeSheet:
        return next(s for s in self.spreadsheets[spreadsheet_id] if s.title == title)

    @property
    def write_calls(self) -> list[str]:
        writes = ("create_file", "batch_update", "write_values", "append_values", "upload_file")
        return [c for c in self.calls if c in writes]

    # --- SheetsApi ---------------------------------------------------------

    def find_file_ids(self, marker: str, mime_type: str) -> list[str]:
        self.calls.append("find_file_ids")
        return [
            file_id
            for file_id, f in self.files.items()
            if f.marker == marker and f.mime_type == mime_type and not f.trashed
        ]

    def create_file(self, name: str, mime_type: str, marker: str, parent_id: str | None) -> str:
        self.calls.append("create_file")
        file_id = f"file-{len(self.files) + 1}"
        self.files[file_id] = FakeFile(name, mime_type, marker, parent_id)
        if mime_type == SPREADSHEET_MIME_TYPE:
            self.spreadsheets[file_id] = [FakeSheet(sheet_id=0, title=self.starter_sheet_title)]
        return file_id

    def upload_file(self, name: str, media_type: str, data: bytes, parent_id: str, marker: str) -> str:
        self.calls.append("upload_file")
        file_id = f"file-{len(self.files) + 1}"
        self.files[file_id] = FakeFile(name, media_type, marker, parent_id)
        self.uploads[file_id] = data
        return f"https://drive.google.com/file/d/{file_id}/view"

    def get_sheets(self, spreadsheet_id: str) -> list[SheetInfo]:
        self.calls.append("get_sheets")
        return [
            SheetInfo(
                sheet_id=s.sheet_id,
                title=s.title,
                column_count=s.column_count,
                headers=[h.strip() for h in s.rows[0]] if s.rows else [],
            )
            for s in self.spreadsheets[spreadsheet_id]
        ]

    def batch_update(self, spreadsheet_id: str, requests: list[dict[str, Any]]) -> None:
        self.calls.append("batch_update")
        sheets = self.spreadsheets[spreadsheet_id]
        for request in requests:
            if "addSheet" in request:
                props = request["addSheet"]["properties"]
                sheets.append(
                    FakeSheet(
                        sheet_id=max(s.sheet_id for s in sheets) + 1,
                        title=props["title"],
                        frozen_rows=props.get("gridProperties", {}).get("frozenRowCount", 0),
                    )
                )
            elif "updateSheetProperties" in request:
                props = request["updateSheetProperties"]["properties"]
                target = next(s for s in sheets if s.sheet_id == props["sheetId"])
                target.title = props["title"]
                target.frozen_rows = props.get("gridProperties", {}).get("frozenRowCount", 0)
            elif "appendDimension" in request:
                dim = request["appendDimension"]
                target = next(s for s in sheets if s.sheet_id == dim["sheetId"])
                target.column_count += dim["length"]
            else:
                raise AssertionError(f"Unexpected request: {request}")

    def write_values(self, spreadsheet_id: str, data: list[tuple[str, list[list[str]]]]) -> None:
        self.calls.append("write_values")
        for range_, values in data:
            title, cell = _split_range(range_)
            sheet = self.sheet(spreadsheet_id, title)
            row, col = a1_to_rowcol(cell)
            for r_offset, row_values in enumerate(values):
                r = row - 1 + r_offset
                while len(sheet.rows) <= r:
                    sheet.rows.append([])
                target = sheet.rows[r]
                for c_offset, value in enumerate(row_values):
                    c = col - 1 + c_offset
                    if c >= sheet.column_count:
                        raise AssertionError(f"Column {c + 1} is outside the grid of {title}")
                    while len(target) <= c:
                        target.append("")
                    target[c] = value

    def append_values(self, spreadsheet_id: str, range_: str, rows: list[list[str]]) -> None:
        self.calls.append("append_values")
        title, _ = _split_range(range_)
        sheet = self.sheet(spreadsheet_id, title)
        while sheet.rows and not any(cell.strip() for cell in sheet.rows[-1]):
            sheet.rows.pop()
        sheet.rows.extend(list(row) for row in rows)

    def read_values(self, spreadsheet_id: str, range_: str) -> list[list[str]]:
        self.calls.append("read_values")
        title, _ = _split_range(range_)
        return [list(row) for row in self.sheet(spreadsheet_id, title).rows]


def _split_range(range_: str) -> tuple[str, str]:
    title, _, cell = range_.partition("!")
    if title.startswith("'") and title.endswith("'"):
        title = title[1:-1].replace("''", "'")
    return title, cell
