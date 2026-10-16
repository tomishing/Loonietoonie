import pytest

from app.sheets import setup
from app.sheets.schema import (
    ADDED_SUBCATEGORIES,
    DEFAULT_CATEGORIES,
    DEFAULT_SUBCATEGORIES,
    FOLDER_MARKER,
    FOLDER_MIME_TYPE,
    SPREADSHEET_MARKER,
    SPREADSHEET_MIME_TYPE,
    SUBCATEGORY_DESCRIPTIONS,
    TABS,
)
from app.sheets.setup import ensure_spreadsheet, migrate_once
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
    assert len(rows) == 15
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
            # A tab from an older version (budgets were dropped): kept, like any extra tab.
            FakeSheet(3, "budgets", rows=[["month", "category", "amount", "updated_at", "deleted"]]),
            FakeSheet(4, "settings", rows=[list(TABS["settings"])]),
            FakeSheet(5, "My notes", rows=[["anything"]]),
        ],
    )

    ensure_spreadsheet(api, NOW)

    items = api.sheet("sheet-1", "items")
    assert items.rows[0] == [*items_headers, "quantity", "subcategory", "updated_at", "deleted"]
    assert items.rows[1] == ["i1", "r1", "Milk", "3.49", "Groceries"]
    assert api.sheet("sheet-1", "ocr_labels").rows == [list(TABS["ocr_labels"])]
    assert api.sheet("sheet-1", "My notes").rows == [["anything"]]
    assert api.sheet("sheet-1", "budgets").rows == [["month", "category", "amount", "updated_at", "deleted"]]
    # categories tab already existed: the user's list is kept; only required categories are added
    assert api.sheet("sheet-1", "categories").rows[1:] == [
        ["Snacks", "🍪", NOW, "FALSE"],
        ["Tax & Fees", "🧾", NOW, "FALSE"],
        ["Travel", "✈️", NOW, "FALSE"],
    ]
    # the new subcategories tab starts with the defaults
    subcategories = api.sheet("sheet-1", "subcategories").rows
    assert subcategories[0] == list(TABS["subcategories"])
    fruit = ("Groceries", "Fresh Fruit & Vegetables")
    assert [*fruit, SUBCATEGORY_DESCRIPTIONS[fruit], NOW, "FALSE"] in subcategories
    assert len(subcategories) - 1 == sum(len(names) for names in DEFAULT_SUBCATEGORIES.values())


# The subcategories header as first shipped, before the description column.
OLD_SUBCATEGORY_HEADER = ["category", "name", "updated_at", "deleted"]


def existing_spreadsheet_with_subcategories(
    api: FakeSheetsApi, rows: list[list[str]], header: list[str] = OLD_SUBCATEGORY_HEADER
) -> None:
    sheets = [FakeSheet(i, tab, rows=[list(columns)]) for i, (tab, columns) in enumerate(TABS.items())]
    sheets[3] = FakeSheet(3, "subcategories", rows=[list(header), *rows])
    api.add_spreadsheet("sheet-1", sheets)


def describe(category: str, name: str) -> str:
    return SUBCATEGORY_DESCRIPTIONS[(category, name)]


def test_existing_subcategories_tab_gets_description_column_and_added_defaults() -> None:
    api = FakeSheetsApi()
    existing_spreadsheet_with_subcategories(api, [["Pets", "Toys", "2026-01-01T00:00:00Z", "FALSE"]])

    ensure_spreadsheet(api, NOW)

    rows = api.sheet("sheet-1", "subcategories").rows
    assert rows[0] == [*OLD_SUBCATEGORY_HEADER, "description"]
    assert rows[1:] == [
        ["Pets", "Toys", "2026-01-01T00:00:00Z", "FALSE"],  # the user's own row is untouched
        *[[c, n, NOW, "FALSE", describe(c, n)] for c, n in ADDED_SUBCATEGORIES],
    ]


def test_defaults_are_renamed_and_described_once() -> None:
    api = FakeSheetsApi()
    old = "2026-01-01T00:00:00Z"
    existing_spreadsheet_with_subcategories(
        api, [["Groceries", "Produce", old, "FALSE"], ["Groceries", "Bakery", old, "FALSE"]]
    )

    ensure_spreadsheet(api, NOW)
    rows = api.sheet("sheet-1", "subcategories").rows[1:]
    api.calls.clear()
    ensure_spreadsheet(api, NOW)

    fruit = "Fresh Fruit & Vegetables"
    assert rows[:2] == [
        ["Groceries", fruit, NOW, "FALSE", describe("Groceries", fruit)],
        ["Groceries", "Bakery", NOW, "FALSE", describe("Groceries", "Bakery")],
    ]
    assert "write_values" not in api.calls and "append_values" not in api.calls  # nothing left to do


def test_user_choices_about_defaults_are_respected() -> None:
    api = FakeSheetsApi()
    existing_spreadsheet_with_subcategories(
        api,
        [
            ["Groceries", "Produce", "", NOW, "TRUE"],  # soft-deleted: not renamed
            ["groceries", "tofu & plant protein", "", NOW, "TRUE"],  # soft-deleted: not re-added
            ["Groceries", "Rice, Grains & Cereal", "", NOW, "FALSE"],  # already added by hand
            ["Groceries", "Pantry", "Oils and sauces only", NOW, "FALSE"],  # own description kept
        ],
        header=list(TABS["subcategories"]),
    )

    ensure_spreadsheet(api, NOW)

    rows = api.sheet("sheet-1", "subcategories").rows[1:]
    groceries = [row for row in rows if row[0].casefold() == "groceries"]
    assert [row[1] for row in groceries] == [
        "Produce",
        "tofu & plant protein",
        "Rice, Grains & Cereal",
        "Pantry",
    ]
    assert rows[3][2] == "Oils and sauces only"
    assert rows[2][2] == describe("Groceries", "Rice, Grains & Cereal")  # blank description filled


def test_migrate_once_runs_once_per_spreadsheet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(setup, "_migrated", set())
    api = FakeSheetsApi()
    api.add_spreadsheet("sheet-1", [FakeSheet(0, "receipts", rows=[list(TABS["receipts"])])])

    migrate_once(api, "sheet-1", NOW)
    assert api.sheet("sheet-1", "subcategories").rows[0] == list(TABS["subcategories"])
    api.calls.clear()
    migrate_once(api, "sheet-1", NOW)

    assert api.calls == []


def existing_spreadsheet_with_categories(api: FakeSheetsApi, category_rows: list[list[str]]) -> None:
    sheets = [FakeSheet(i, tab, rows=[list(columns)]) for i, (tab, columns) in enumerate(TABS.items())]
    sheets[2] = FakeSheet(2, "categories", rows=[list(TABS["categories"]), *category_rows])
    # Subcategories already up to date, so only the categories migration can write.
    added = [[c, n, SUBCATEGORY_DESCRIPTIONS[(c, n)], NOW, "FALSE"] for c, n in ADDED_SUBCATEGORIES]
    sheets[3] = FakeSheet(3, "subcategories", rows=[list(TABS["subcategories"]), *added])
    api.add_spreadsheet("sheet-1", sheets)


def test_required_category_is_added_once() -> None:
    api = FakeSheetsApi()
    existing_spreadsheet_with_categories(api, [["Groceries", "🛒", NOW, "FALSE"]])

    ensure_spreadsheet(api, NOW)
    api.calls.clear()
    ensure_spreadsheet(api, NOW)

    names = [row[0] for row in api.sheet("sheet-1", "categories").rows[1:]]
    assert names == ["Groceries", "Tax & Fees", "Travel"]
    assert api.write_calls == []


def test_soft_deleted_required_category_is_not_re_added() -> None:
    api = FakeSheetsApi()
    existing_spreadsheet_with_categories(
        api, [["tax & fees", "🧾", NOW, "TRUE"], ["travel", "✈️", NOW, "TRUE"]]
    )

    ensure_spreadsheet(api, NOW)

    assert len(api.sheet("sheet-1", "categories").rows) == 3
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
