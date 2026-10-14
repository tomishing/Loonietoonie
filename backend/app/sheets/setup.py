"""Find or create the user's LoonieToonie spreadsheet and bring its tabs up to date."""

import threading
from typing import Any

from gspread.utils import rowcol_to_a1

from app.sheets.api import SheetInfo, SheetsApi
from app.sheets.schema import (
    DEFAULT_CATEGORIES,
    DEFAULT_SUBCATEGORIES,
    FALSE,
    FOLDER_MARKER,
    FOLDER_MIME_TYPE,
    FOLDER_NAME,
    REQUIRED_CATEGORIES,
    SPREADSHEET_MARKER,
    SPREADSHEET_MIME_TYPE,
    SPREADSHEET_NAME,
    TABS,
    tab_range,
)

# Spreadsheets already migrated by this server process (see migrate_once).
_migrated: set[str] = set()
_migrated_lock = threading.Lock()


def ensure_spreadsheet(api: SheetsApi, now: str) -> str:
    """Return the user's spreadsheet ID, creating the folder and spreadsheet on first sign-in."""
    existing = api.find_file_ids(SPREADSHEET_MARKER, SPREADSHEET_MIME_TYPE)
    if existing:
        spreadsheet_id = existing[0]
    else:
        folders = api.find_file_ids(FOLDER_MARKER, FOLDER_MIME_TYPE)
        folder_id = (
            folders[0] if folders else api.create_file(FOLDER_NAME, FOLDER_MIME_TYPE, FOLDER_MARKER, None)
        )
        spreadsheet_id = api.create_file(
            SPREADSHEET_NAME, SPREADSHEET_MIME_TYPE, SPREADSHEET_MARKER, folder_id
        )

    migrate(api, spreadsheet_id, now)
    with _migrated_lock:
        _migrated.add(spreadsheet_id)
    return spreadsheet_id


def migrate(api: SheetsApi, spreadsheet_id: str, now: str) -> None:
    """Add missing tabs and missing header columns. Existing data and column order are kept.

    Uses at most two reads and two writes, and none of the writes when nothing changed.
    """
    sheets = api.get_sheets(spreadsheet_id)
    by_title = {sheet.title: sheet for sheet in sheets}
    requests: list[dict[str, Any]] = []
    values: list[tuple[str, list[list[str]]]] = []
    created: set[str] = set()

    # A brand-new spreadsheet has one empty tab ("Sheet1", or a localized name). Reuse it.
    blank = _blank_starter_sheet(sheets)

    for tab, columns in TABS.items():
        sheet = by_title.get(tab)
        if sheet is None:
            if blank is not None:
                requests.append(_rename_request(blank, tab))
                blank = None
            else:
                requests.append(
                    {"addSheet": {"properties": {"title": tab, "gridProperties": {"frozenRowCount": 1}}}}
                )
            values.append((tab_range(tab, "A1"), [list(columns)]))
            created.add(tab)
            continue

        missing = [column for column in columns if column not in sheet.headers]
        if missing:
            first_new_column = len(sheet.headers) + 1
            needed = len(sheet.headers) + len(missing)
            if needed > sheet.column_count:
                requests.append(
                    {
                        "appendDimension": {
                            "sheetId": sheet.sheet_id,
                            "dimension": "COLUMNS",
                            "length": needed - sheet.column_count,
                        }
                    }
                )
            values.append((tab_range(tab, rowcol_to_a1(1, first_new_column)), [missing]))

    if "categories" in created:
        rows = [[name, icon, now, FALSE] for name, icon in DEFAULT_CATEGORIES]
        values.append((tab_range("categories", "A2"), rows))
    if "subcategories" in created:
        rows = [
            [category, name, now, FALSE]
            for category, names in DEFAULT_SUBCATEGORIES.items()
            for name in names
        ]
        values.append((tab_range("subcategories", "A2"), rows))

    if requests:
        api.batch_update(spreadsheet_id, requests)
    if values:
        api.write_values(spreadsheet_id, values)

    if "categories" not in created:
        _add_required_categories(api, spreadsheet_id, now)


def migrate_once(api: SheetsApi, spreadsheet_id: str, now: str) -> None:
    """Migrate a spreadsheet the first time this server process uses it.

    Sign-in already migrates, but sessions last for weeks: this brings a spreadsheet up to
    date after a schema change without asking everyone to sign in again.
    """
    with _migrated_lock:
        if spreadsheet_id in _migrated:
            return
    migrate(api, spreadsheet_id, now)
    with _migrated_lock:
        _migrated.add(spreadsheet_id)


def _add_required_categories(api: SheetsApi, spreadsheet_id: str, now: str) -> None:
    """Data migration: append categories the app relies on (one read, at most one write)."""
    rows = api.read_values(spreadsheet_id, tab_range("categories"))
    headers = [header.strip() for header in rows[0]] if rows else []
    if "name" not in headers:
        return
    name_index = headers.index("name")
    existing = {row[name_index].strip().casefold() for row in rows[1:] if len(row) > name_index}
    missing = [(name, icon) for name, icon in REQUIRED_CATEGORIES if name.casefold() not in existing]
    if missing:
        records = [
            {"name": name, "icon": icon, "updated_at": now, "deleted": FALSE} for name, icon in missing
        ]
        rows_to_add = [[record.get(header, "") for header in headers] for record in records]
        api.append_values(spreadsheet_id, tab_range("categories"), rows_to_add)


def _blank_starter_sheet(sheets: list[SheetInfo]) -> SheetInfo | None:
    if len(sheets) == 1 and sheets[0].title not in TABS and not any(sheets[0].headers):
        return sheets[0]
    return None


def _rename_request(sheet: SheetInfo, title: str) -> dict[str, Any]:
    return {
        "updateSheetProperties": {
            "properties": {
                "sheetId": sheet.sheet_id,
                "title": title,
                "gridProperties": {"frozenRowCount": 1},
            },
            "fields": "title,gridProperties.frozenRowCount",
        }
    }
