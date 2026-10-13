from datetime import date
from decimal import Decimal

import pytest

from app.sheets.schema import TABS
from app.sheets.setup import ensure_spreadsheet
from app.storage.models import ItemDraft, ReceiptDraft, ReceiptFilter
from app.storage.repository import StorageError
from app.storage.sheets_repository import SheetsRepository, TabReadCache
from tests.fakes import FakeSheet, FakeSheetsApi

NOW = "2026-10-13T09:30:00Z"


def setup_repo() -> tuple[SheetsRepository, FakeSheetsApi, str]:
    api = FakeSheetsApi()
    spreadsheet_id = ensure_spreadsheet(api, NOW)
    api.calls.clear()
    return SheetsRepository(api, spreadsheet_id, TabReadCache()), api, spreadsheet_id


def draft(
    store: str = "FreshMart",
    purchased_at: date = date(2026, 10, 1),
    total: str = "7.47",
    items: list[ItemDraft] | None = None,
    currency: str = "USD",
) -> ReceiptDraft:
    return ReceiptDraft(
        store=store,
        purchased_at=purchased_at,
        total=Decimal(total),
        currency=currency,
        items=items
        or [
            ItemDraft(name="Milk", quantity=Decimal(1), price=Decimal("3.49"), category="Groceries"),
            ItemDraft(name="Soap", quantity=Decimal(2), price=Decimal("1.99"), category="Household"),
        ],
    )


def data_rows(api: FakeSheetsApi, spreadsheet_id: str, tab: str) -> list[dict[str, str]]:
    rows = api.sheet(spreadsheet_id, tab).rows
    return [dict(zip(rows[0], row, strict=False)) for row in rows[1:]]


def test_create_writes_receipt_and_items_with_uuids_in_two_appends() -> None:
    repo, api, sid = setup_repo()

    receipt = repo.create_receipt(draft())

    assert api.write_calls == ["append_values", "append_values"]
    [receipt_row] = data_rows(api, sid, "receipts")
    assert receipt_row["receipt_id"] == receipt.receipt_id
    assert len(receipt.receipt_id) == 36
    assert receipt_row["total"] == "7.47"
    assert receipt_row["purchased_at"] == "2026-10-01"
    assert receipt_row["deleted"] == "FALSE"
    assert receipt_row["created_at"] == receipt_row["updated_at"]
    item_rows = data_rows(api, sid, "items")
    assert [r["name"] for r in item_rows] == ["Milk", "Soap"]
    assert {r["receipt_id"] for r in item_rows} == {receipt.receipt_id}
    assert item_rows[1]["quantity"] == "2"
    assert len({r["item_id"] for r in item_rows}) == 2


def test_create_respects_user_column_order_and_extra_columns() -> None:
    api = FakeSheetsApi()
    receipt_headers = [
        "store",
        "receipt_id",
        "my_note",
        *[c for c in TABS["receipts"] if c not in ("store", "receipt_id")],
    ]
    api.add_spreadsheet(
        "s1",
        [
            FakeSheet(0, "receipts", rows=[receipt_headers]),
            FakeSheet(1, "items", rows=[list(TABS["items"])]),
            FakeSheet(
                2,
                "categories",
                rows=[
                    list(TABS["categories"]),
                    ["Groceries", "🛒", NOW, "FALSE"],
                    ["Household", "🧻", NOW, "FALSE"],
                ],
            ),
        ],
    )
    repo = SheetsRepository(api, "s1", TabReadCache())

    receipt = repo.create_receipt(draft())

    row = api.sheet("s1", "receipts").rows[1]
    assert row[0] == "FreshMart"
    assert row[1] == receipt.receipt_id
    assert row[2] == ""


def test_list_joins_items_sorts_newest_first_and_skips_deleted() -> None:
    repo, _, _ = setup_repo()
    older = repo.create_receipt(draft(store="Old", purchased_at=date(2026, 9, 1)))
    newer = repo.create_receipt(draft(store="New", purchased_at=date(2026, 10, 5)))
    gone = repo.create_receipt(draft(store="Gone", purchased_at=date(2026, 10, 6)))
    repo.delete_receipt(gone.receipt_id)

    receipts = repo.list_receipts(ReceiptFilter())

    assert [r.receipt_id for r in receipts] == [newer.receipt_id, older.receipt_id]
    assert [i.name for i in receipts[0].items] == ["Milk", "Soap"]
    assert receipts[0].items[1].price == Decimal("1.99")


def test_list_filters_by_date_range_and_category() -> None:
    repo, _, _ = setup_repo()
    september = repo.create_receipt(draft(purchased_at=date(2026, 9, 15)))
    october = repo.create_receipt(
        draft(
            purchased_at=date(2026, 10, 3),
            total="12",
            items=[ItemDraft(name="Ramen", price=Decimal(12), category="Dining Out")],
        )
    )

    in_october = repo.list_receipts(ReceiptFilter(date_from=date(2026, 10, 1), date_to=date(2026, 10, 31)))
    dining = repo.list_receipts(ReceiptFilter(category="dining out"))
    groceries_until_sept = repo.list_receipts(ReceiptFilter(category="Groceries", date_to=date(2026, 9, 30)))

    assert [r.receipt_id for r in in_october] == [october.receipt_id]
    assert [r.receipt_id for r in dining] == [october.receipt_id]
    assert [r.receipt_id for r in groceries_until_sept] == [september.receipt_id]


def test_get_receipt() -> None:
    repo, _, _ = setup_repo()
    created = repo.create_receipt(draft())

    assert repo.get_receipt(created.receipt_id) == created
    assert repo.get_receipt("missing") is None


def test_update_changes_fields_updates_adds_and_soft_deletes_items() -> None:
    repo, api, sid = setup_repo()
    created = repo.create_receipt(draft())
    milk, soap = created.items
    api.calls.clear()

    updated = repo.update_receipt(
        created.receipt_id,
        draft(
            store="FreshMart Downtown",
            total="9.98",
            currency="CAD",
            items=[
                ItemDraft(item_id=milk.item_id, name="Oat milk", price=Decimal("4.99"), category="Groceries"),
                ItemDraft(name="Bread", price=Decimal("4.99"), category="Groceries"),
            ],
        ),
    )

    assert updated is not None
    assert updated.store == "FreshMart Downtown"
    assert updated.currency == "CAD"
    assert updated.created_at == created.created_at
    assert [i.name for i in updated.items] == ["Oat milk", "Bread"]
    assert updated.items[0].item_id == milk.item_id
    # 2 fresh reads, 1 batch rewrite of changed rows, 1 append for the new item
    assert api.calls == ["read_values", "read_values", "write_values", "append_values"]

    items = {r["item_id"]: r for r in data_rows(api, sid, "items")}
    assert items[milk.item_id]["name"] == "Oat milk"
    assert items[milk.item_id]["deleted"] == "FALSE"
    assert items[soap.item_id]["deleted"] == "TRUE"
    assert len(items) == 3
    assert repo.get_receipt(created.receipt_id) == updated


def test_update_keeps_columns_the_app_does_not_know() -> None:
    repo, api, sid = setup_repo()
    created = repo.create_receipt(draft())
    sheet = api.sheet(sid, "receipts")
    sheet.rows[0].append("my_note")
    sheet.rows[1].append("birthday party")

    repo.update_receipt(
        created.receipt_id,
        draft(
            store="Changed",
            items=[
                ItemDraft(
                    item_id=created.items[0].item_id, name="Milk", price=Decimal("3.49"), category="Groceries"
                )
            ],
        ),
    )

    row = dict(zip(sheet.rows[0], sheet.rows[1], strict=False))
    assert row["store"] == "Changed"
    assert row["my_note"] == "birthday party"


def test_update_uses_fresh_rows_not_stale_cache() -> None:
    repo, api, sid = setup_repo()
    created = repo.create_receipt(draft())
    repo.list_receipts(ReceiptFilter())  # warm the cache
    # The user inserts a row above ours directly in the sheet.
    api.sheet(sid, "receipts").rows.insert(
        1, ["other-id", "Manual", "2026-01-01", "1", "USD", "", NOW, NOW, "FALSE"]
    )

    repo.update_receipt(
        created.receipt_id,
        draft(
            store="Changed",
            items=[
                ItemDraft(
                    item_id=created.items[0].item_id, name="Milk", price=Decimal("3.49"), category="Groceries"
                )
            ],
        ),
    )

    rows = data_rows(api, sid, "receipts")
    assert rows[0]["store"] == "Manual"
    assert rows[1]["store"] == "Changed"


def test_update_missing_receipt_returns_none() -> None:
    repo, _, _ = setup_repo()

    assert repo.update_receipt("missing", draft()) is None


def test_delete_soft_deletes_receipt_and_items_in_one_write() -> None:
    repo, api, sid = setup_repo()
    created = repo.create_receipt(draft())
    api.calls.clear()

    assert repo.delete_receipt(created.receipt_id) is True

    assert api.write_calls == ["write_values"]
    assert data_rows(api, sid, "receipts")[0]["deleted"] == "TRUE"
    assert {r["deleted"] for r in data_rows(api, sid, "items")} == {"TRUE"}
    assert repo.get_receipt(created.receipt_id) is None
    assert repo.delete_receipt(created.receipt_id) is False


def test_rows_typed_by_hand_are_parsed_and_bad_rows_skipped() -> None:
    repo, api, sid = setup_repo()
    receipts = api.sheet(sid, "receipts").rows
    receipts.append(["r1", "Corner Shop", "2026-10-02", "1,234.5", "jpy", "", NOW, NOW, "false"])
    receipts.append(["r2", "Broken", "next tuesday", "5", "USD", "", NOW, NOW, "FALSE"])
    api.sheet(sid, "items").rows.append(["i1", "r1", "Onigiri", "", "1234.5", "Groceries", NOW, ""])

    [receipt] = repo.list_receipts(ReceiptFilter())

    assert receipt.store == "Corner Shop"
    assert receipt.total == Decimal("1234.5")
    assert receipt.currency == "JPY"
    assert receipt.items[0].quantity == Decimal(1)


def test_missing_column_raises_storage_error() -> None:
    api = FakeSheetsApi()
    api.add_spreadsheet(
        "s1",
        [
            FakeSheet(0, "receipts", rows=[["receipt_id", "store"]]),
            FakeSheet(1, "items", rows=[list(TABS["items"])]),
        ],
    )
    repo = SheetsRepository(api, "s1", TabReadCache())

    with pytest.raises(StorageError):
        repo.create_receipt(draft())
