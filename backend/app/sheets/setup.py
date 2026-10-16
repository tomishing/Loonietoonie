"""Find or create the user's LoonieToonie spreadsheet and bring its tabs up to date."""

import threading
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from gspread.utils import rowcol_to_a1

from app.sheets.api import SheetInfo, SheetsApi
from app.sheets.schema import (
    ADDED_CATEGORIES,
    ADDED_SUBCATEGORIES,
    DEFAULT_CATEGORIES,
    DEFAULT_SUBCATEGORIES,
    FALSE,
    FOLDER_MARKER,
    FOLDER_MIME_TYPE,
    FOLDER_NAME,
    OLD_DESCRIPTIONS,
    RENAMED_SUBCATEGORIES,
    REQUIRED_CATEGORIES,
    SPREADSHEET_MARKER,
    SPREADSHEET_MIME_TYPE,
    SPREADSHEET_NAME,
    SUBCATEGORY_DESCRIPTIONS,
    TABS,
    TRUE,
    tab_range,
)

# Spreadsheets already migrated by this server process (see migrate_once).
_migrated: set[str] = set()
_migrated_lock = threading.Lock()
# One migration at a time per spreadsheet: the app sends several requests at once, and two
# migrations running together would both append the same new defaults.
_migration_locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)


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
    with _migrated_lock:
        lock = _migration_locks[spreadsheet_id]
    with lock:
        _migrate(api, spreadsheet_id, now)


def _migrate(api: SheetsApi, spreadsheet_id: str, now: str) -> None:
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
        records = [
            _subcategory_record(category, name, now)
            for category, names in DEFAULT_SUBCATEGORIES.items()
            for name in names
        ]
        rows = [[record[column] for column in TABS["subcategories"]] for record in records]
        values.append((tab_range("subcategories", "A2"), rows))

    if requests:
        api.batch_update(spreadsheet_id, requests)
    if values:
        api.write_values(spreadsheet_id, values)

    if "categories" not in created:
        _add_required_categories(api, spreadsheet_id, now)
    if "subcategories" not in created:
        _update_default_subcategories(api, spreadsheet_id, now)


def migrate_once(api: SheetsApi, spreadsheet_id: str, now: str) -> None:
    """Migrate a spreadsheet the first time this server process uses it.

    Sign-in already migrates, but sessions last for weeks: this brings a spreadsheet up to
    date after a schema change without asking everyone to sign in again.
    """
    with _migrated_lock:
        if spreadsheet_id in _migrated:
            return
        lock = _migration_locks[spreadsheet_id]
    with lock:
        with _migrated_lock:
            if spreadsheet_id in _migrated:
                return  # another request migrated it while this one waited
        _migrate(api, spreadsheet_id, now)
        with _migrated_lock:
            _migrated.add(spreadsheet_id)


def _add_required_categories(api: SheetsApi, spreadsheet_id: str, now: str) -> None:
    """Data migration: append categories the app relies on and default categories added since
    the first release, and soft-delete extra copies of them that two migrations running at once
    appended before migrations were locked (one read, at most two writes)."""
    rows = api.read_values(spreadsheet_id, tab_range("categories"))
    headers = [header.strip() for header in rows[0]] if rows else []
    if "name" not in headers:
        return
    name_index = headers.index("name")
    existing = {row[name_index].strip().casefold() for row in rows[1:] if len(row) > name_index}
    added = (*REQUIRED_CATEGORIES, *ADDED_CATEGORIES)
    _remove_duplicates(
        api,
        spreadsheet_id,
        "categories",
        headers,
        rows,
        lambda row: _cell(headers, row, "name").casefold(),
        {name.casefold() for name, _ in added},
        now,
    )
    missing = [(name, icon) for name, icon in added if name.casefold() not in existing]
    if missing:
        records = [
            {"name": name, "icon": icon, "updated_at": now, "deleted": FALSE} for name, icon in missing
        ]
        rows_to_add = [[record.get(header, "") for header in headers] for record in records]
        api.append_values(spreadsheet_id, tab_range("categories"), rows_to_add)


def _cell(headers: list[str], row: list[str], header: str) -> str:
    index = headers.index(header) if header in headers else None
    return row[index].strip() if index is not None and index < len(row) else ""


def _remove_duplicates(
    api: SheetsApi,
    spreadsheet_id: str,
    tab: str,
    headers: list[str],
    rows: list[list[str]],
    key: Callable[[list[str]], Any],
    keys: set[Any],
    now: str,
) -> None:
    """Soft-delete active rows that repeat an earlier active row with the same key, for the given
    keys only (defaults the app appends). The first copy is kept. At most one write."""
    if "deleted" not in headers:
        return
    seen: set[Any] = set()
    updates: list[tuple[str, list[list[str]]]] = []
    for number, row in enumerate(rows[1:], start=2):
        row_key = key(row)
        if row_key not in keys or _cell(headers, row, "deleted").upper() == TRUE:
            continue
        if row_key in seen:
            for header, value in (("deleted", TRUE), ("updated_at", now)):
                if header in headers:
                    cell = rowcol_to_a1(number, headers.index(header) + 1)
                    updates.append((tab_range(tab, cell), [[value]]))
        seen.add(row_key)
    if updates:
        api.write_values(spreadsheet_id, updates)


def _subcategory_record(category: str, name: str, now: str) -> dict[str, str]:
    return {
        "category": category,
        "name": name,
        "description": SUBCATEGORY_DESCRIPTIONS.get((category, name), ""),
        "updated_at": now,
        "deleted": FALSE,
    }


def _update_default_subcategories(api: SheetsApi, spreadsheet_id: str, now: str) -> None:
    """Data migration: bring the defaults in an existing subcategories tab up to date.

    - renames defaults renamed since (RENAMED_SUBCATEGORIES)
    - fills blank descriptions of default subcategories (SUBCATEGORY_DESCRIPTIONS), and replaces
      old default descriptions the user never edited (OLD_DESCRIPTIONS)
    - appends defaults added since (ADDED_SUBCATEGORIES)

    One read, at most two writes. Rows the user added, renamed, described, or soft-deleted
    are respected.
    """
    rows = api.read_values(spreadsheet_id, tab_range("subcategories"))
    headers = [header.strip() for header in rows[0]] if rows else []
    if "category" not in headers or "name" not in headers:
        return
    column = {header: index for index, header in enumerate(headers) if header}

    def cell(row: list[str], header: str) -> str:
        index = column.get(header)
        return row[index].strip() if index is not None and index < len(row) else ""

    def key(category: str, name: str) -> tuple[str, str]:
        return category.casefold(), name.casefold()

    _remove_duplicates(
        api,
        spreadsheet_id,
        "subcategories",
        headers,
        rows,
        lambda row: key(cell(row, "category"), cell(row, "name")),
        {key(category, name) for category, name in ADDED_SUBCATEGORIES},
        now,
    )
    renamed = {key(category, old): new for (category, old), new in RENAMED_SUBCATEGORIES.items()}
    descriptions = {key(category, name): text for (category, name), text in SUBCATEGORY_DESCRIPTIONS.items()}
    old_descriptions = {key(category, name): text for (category, name), text in OLD_DESCRIPTIONS.items()}
    existing = {key(cell(row, "category"), cell(row, "name")) for row in rows[1:]}

    updates: list[tuple[str, list[list[str]]]] = []

    def update(number: int, header: str, value: str) -> None:
        if header in column:
            updates.append((tab_range("subcategories", rowcol_to_a1(number, column[header] + 1)), [[value]]))

    for number, row in enumerate(rows[1:], start=2):
        category, name = cell(row, "category"), cell(row, "name")
        changed = False
        new_name = renamed.get(key(category, name))
        if (
            new_name is not None
            and cell(row, "deleted").upper() != TRUE
            and key(category, new_name) not in existing
        ):
            update(number, "name", new_name)
            existing.add(key(category, new_name))
            name, changed = new_name, True
        description = descriptions.get(key(category, name))
        current = cell(row, "description")
        if description is not None and (not current or current == old_descriptions.get(key(category, name))):
            update(number, "description", description)
            changed = True
        if changed:
            update(number, "updated_at", now)

    missing = [
        _subcategory_record(category, name, now)
        for category, name in ADDED_SUBCATEGORIES
        if key(category, name) not in existing
    ]
    if updates:
        api.write_values(spreadsheet_id, updates)
    if missing:
        api.append_values(
            spreadsheet_id,
            tab_range("subcategories"),
            [[record.get(header, "") for header in headers] for record in missing],
        )


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
