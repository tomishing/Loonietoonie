import pytest
from gspread.exceptions import APIError

from app.storage.models import Category
from app.storage.repository import StorageError
from app.storage.sheets_repository import SheetsRepository, TabReadCache
from tests.fakes import FakeSheet, FakeSheetsApi


def make_repo(
    category_rows: list[list[str]], cache: TabReadCache | None = None
) -> tuple[SheetsRepository, FakeSheetsApi]:
    api = FakeSheetsApi()
    api.add_spreadsheet("sheet-1", [FakeSheet(0, "categories", rows=category_rows)])
    return SheetsRepository(api, "sheet-1", cache or TabReadCache()), api


def test_list_categories_skips_deleted_and_blank_rows() -> None:
    repo, _ = make_repo(
        [
            ["name", "icon", "updated_at", "deleted"],
            ["Groceries", "🛒", "2026-10-13T00:00:00Z", "FALSE"],
            ["Old", "🗑", "2026-10-13T00:00:00Z", "TRUE"],
            ["", "", "", ""],
            ["Pets", "🐾", "2026-10-13T00:00:00Z", "false"],
            [" ", "❓", "", "FALSE"],
        ]
    )

    assert repo.list_categories() == [Category(name="Groceries", icon="🛒"), Category(name="Pets", icon="🐾")]


def test_list_categories_handles_reordered_columns_and_short_rows() -> None:
    repo, _ = make_repo(
        [["deleted", "name", "updated_at", "icon"], ["FALSE", "Health", "", "💊"], ["", "Kids"]]
    )

    assert repo.list_categories() == [Category(name="Health", icon="💊"), Category(name="Kids", icon="")]


def test_empty_tab_returns_no_categories() -> None:
    repo, _ = make_repo([])

    assert repo.list_categories() == []


def test_reads_are_cached() -> None:
    repo, api = make_repo([["name", "icon", "updated_at", "deleted"], ["Groceries", "🛒", "", "FALSE"]])

    repo.list_categories()
    repo.list_categories()

    assert api.calls.count("read_values") == 1


def test_cache_expires() -> None:
    repo, api = make_repo([["name", "icon"], ["Groceries", "🛒"]], cache=TabReadCache(ttl_seconds=0))

    repo.list_categories()
    repo.list_categories()

    assert api.calls.count("read_values") == 2


def test_google_errors_become_storage_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    repo, api = make_repo([])

    class FakeResponse:
        status_code = 503
        text = "unavailable"

        def json(self) -> dict[str, object]:
            return {"error": {"code": 503, "message": "unavailable", "status": "UNAVAILABLE"}}

    def failing_read(spreadsheet_id: str, range_: str) -> list[list[str]]:
        raise APIError(FakeResponse())  # type: ignore[arg-type]

    monkeypatch.setattr(api, "read_values", failing_read)

    with pytest.raises(StorageError):
        repo.list_categories()
