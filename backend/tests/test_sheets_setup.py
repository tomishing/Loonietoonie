from app.sheets.schema import (
    DEFAULT_CATEGORIES,
    FOLDER_MARKER,
    FOLDER_MIME_TYPE,
    SPREADSHEET_MARKER,
    SPREADSHEET_MIME_TYPE,
    TABS,
)
from app.sheets.setup import ensure_spreadsheet
from tests.fakes import FakeFile, FakeSheet, FakeSheetsApi

NOW = "2026-10-13T09:30:00Z"


def test_first_sign_in_creates_folder_spreadsheet_and_all_tabs() -> None:
    api = FakeSheetsApi()

    spreadsheet_id = ensure_spreadsheet(api, NOW)

    spreadsheet = api.files[spreadsheet_id]
    assert spreadsheet.mime_type == SPREADSHEET_MIME_TYPE
    assert spreadsheet.marker == SPREADSHEET_MARKER
    folder = api.files[spreadsheet.parent_id or ""]
    assert (folder.name, folder.mime_type, folder.marker) == ("LoonieToonie", FOLDER_MIME_TYPE, FOLDER_MARKER)

    sheets = api.spreadsheets[spreadsheet_id]
    assert [s.title for s in sheets] == list(TABS)
    for sheet in sheets:
        assert sheet.rows[0] == list(TABS[sheet.title])
        assert sheet.frozen_rows == 1


def test_first_sign_in_reuses_localized_starter_sheet() -> None:
    api = FakeSheetsApi(starter_sheet_title="シート1")

    spreadsheet_id = ensure_spreadsheet(api, NOW)

    titles = [s.title for s in api.spreadsheets[spreadsheet_id]]
    assert titles == list(TABS)
    assert api.sheet(spreadsheet_id, "receipts").sheet_id == 0


def test_first_sign_in_seeds_default_categories() -> None:
    api = FakeSheetsApi()

    spreadsheet_id = ensure_spreadsheet(api, NOW)

    rows = api.sheet(spreadsheet_id, "categories").rows[1:]
    assert rows == [[name, icon, NOW, "FALSE"] for name, icon in DEFAULT_CATEGORIES]
    assert len(rows) == 14
    assert ["Tax & Fees", "🧾", NOW, "FALSE"] in rows


def test_first_sign_in_uses_few_api_writes() -> None:
    api = FakeSheetsApi()

    ensure_spreadsheet(api, NOW)

    # folder + spreadsheet, one batch_update for all tabs, one write for all headers and categories
    assert api.write_calls == ["create_file", "create_file", "batch_update", "write_values"]


def test_next_sign_in_reuses_spreadsheet_without_writing() -> None:
    api = FakeSheetsApi()
    spreadsheet_id = ensure_spreadsheet(api, NOW)
    api.calls.clear()

    again = ensure_spreadsheet(api, "2026-10-14T00:00:00Z")

    assert again == spreadsheet_id
    assert api.write_calls == []
    assert len(api.sheet(spreadsheet_id, "categories").rows) == 1 + len(DEFAULT_CATEGORIES)


def test_reuses_existing_folder_when_spreadsheet_is_missing() -> None:
    api = FakeSheetsApi()
    api.files["folder-1"] = FakeFile("LoonieToonie", FOLDER_MIME_TYPE, FOLDER_MARKER, None)

    spreadsheet_id = ensure_spreadsheet(api, NOW)

    assert api.files[spreadsheet_id].parent_id == "folder-1"
    assert api.write_calls.count("create_file") == 1


def test_ignores_trashed_spreadsheet() -> None:
    api = FakeSheetsApi()
    api.add_spreadsheet("old", [FakeSheet(0, "receipts")])
    api.files["old"].trashed = True

    spreadsheet_id = ensure_spreadsheet(api, NOW)

    assert spreadsheet_id != "old"


def test_migration_adds_missing_tab_and_columns_and_keeps_data() -> None:
    api = FakeSheetsApi()
    items_headers = ["item_id", "receipt_id", "name", "price", "category"]  # no quantity/updated_at/deleted
    api.add_spreadsheet(
        "sheet-1",
        [
            FakeSheet(0, "receipts", rows=[list(TABS["receipts"])]),
            FakeSheet(1, "items", rows=[list(items_headers), ["i1", "r1", "Milk", "3.49", "Groceries"]]),
            FakeSheet(2, "categories", rows=[list(TABS["categories"]), ["Snacks", "🍪", NOW, "FALSE"]]),
            FakeSheet(3, "budgets", rows=[list(TABS["budgets"])]),
            FakeSheet(4, "settings", rows=[list(TABS["settings"])]),
            FakeSheet(5, "My notes", rows=[["anything"]]),
        ],
    )

    ensure_spreadsheet(api, NOW)

    items = api.sheet("sheet-1", "items")
    assert items.rows[0] == [*items_headers, "quantity", "updated_at", "deleted"]
    assert items.rows[1] == ["i1", "r1", "Milk", "3.49", "Groceries"]
    assert api.sheet("sheet-1", "ocr_labels").rows == [list(TABS["ocr_labels"])]
    assert api.sheet("sheet-1", "My notes").rows == [["anything"]]
    # categories tab already existed: the user's list is kept; only required categories are added
    assert api.sheet("sheet-1", "categories").rows[1:] == [
        ["Snacks", "🍪", NOW, "FALSE"],
        ["Tax & Fees", "🧾", NOW, "FALSE"],
    ]


def existing_spreadsheet_with_categories(api: FakeSheetsApi, category_rows: list[list[str]]) -> None:
    sheets = [FakeSheet(i, tab, rows=[list(columns)]) for i, (tab, columns) in enumerate(TABS.items())]
    sheets[2] = FakeSheet(2, "categories", rows=[list(TABS["categories"]), *category_rows])
    api.add_spreadsheet("sheet-1", sheets)


def test_required_category_is_added_once() -> None:
    api = FakeSheetsApi()
    existing_spreadsheet_with_categories(api, [["Groceries", "🛒", NOW, "FALSE"]])

    ensure_spreadsheet(api, NOW)
    api.calls.clear()
    ensure_spreadsheet(api, NOW)

    names = [row[0] for row in api.sheet("sheet-1", "categories").rows[1:]]
    assert names == ["Groceries", "Tax & Fees"]
    assert api.write_calls == []


def test_soft_deleted_required_category_is_not_re_added() -> None:
    api = FakeSheetsApi()
    existing_spreadsheet_with_categories(api, [["tax & fees", "🧾", NOW, "TRUE"]])

    ensure_spreadsheet(api, NOW)

    assert len(api.sheet("sheet-1", "categories").rows) == 2
    assert api.write_calls == []


def test_required_category_follows_user_column_order() -> None:
    api = FakeSheetsApi()
    sheets = [FakeSheet(i, tab, rows=[list(columns)]) for i, (tab, columns) in enumerate(TABS.items())]
    sheets[2] = FakeSheet(2, "categories", rows=[["icon", "deleted", "name", "updated_at"]])
    api.add_spreadsheet("sheet-1", sheets)

    ensure_spreadsheet(api, NOW)

    assert api.sheet("sheet-1", "categories").rows[1] == ["🧾", "FALSE", "Tax & Fees", NOW]


def test_migration_widens_grid_when_new_columns_do_not_fit() -> None:
    api = FakeSheetsApi()
    sheets = [FakeSheet(i, tab, rows=[list(columns)]) for i, (tab, columns) in enumerate(TABS.items())]
    sheets[1] = FakeSheet(1, "items", column_count=3, rows=[["item_id", "receipt_id", "name"]])
    api.add_spreadsheet("sheet-1", sheets)

    ensure_spreadsheet(api, NOW)

    items = api.sheet("sheet-1", "items")
    assert items.column_count == len(TABS["items"])
    assert items.rows[0] == list(TABS["items"])
