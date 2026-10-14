import csv
import io
from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.services.export import ITEM_COLUMNS, RECEIPT_COLUMNS, UTF8_BOM, export_csv, safe_text
from app.storage.dependencies import get_repository
from app.storage.models import Receipt, ReceiptFilter
from tests.test_auth import make_settings
from tests.test_summary import receipt

RECEIPTS = [
    receipt("2026-09-05", "FreshCo", [("Milk", "5.49", "Groceries"), ("Coupon", "-1.00", "Groceries")]),
    receipt(
        "2026-09-12", "Ramen Isshin", [("Ramen", "18", "Dining Out")], total="20.34"
    ),  # tax only in total
    receipt("2026-10-02", "セブン-イレブン", [("おにぎり", "150", "Groceries")], currency="JPY"),
    receipt("2026-10-03", '=HYPERLINK("http://evil")', [("@SUM(A1)", "1", "Other")]),
]


def rows(text: str) -> list[dict[str, str]]:
    assert text.startswith(UTF8_BOM)
    return list(csv.DictReader(io.StringIO(text[len(UTF8_BOM) :])))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("=1+2", "'=1+2"),
        ("+33 6", "'+33 6"),
        ("-coupon", "'-coupon"),
        ("@SUM(A1)", "'@SUM(A1)"),
        ("\tTab", "'\tTab"),
        ("Milk", "Milk"),
        ("", ""),
        ("Café = good", "Café = good"),
    ],
)
def test_safe_text(value: str, expected: str) -> None:
    assert safe_text(value) == expected


def test_items_export_one_row_per_item_newest_first() -> None:
    result = rows(export_csv(RECEIPTS, "items"))

    assert list(result[0]) == ITEM_COLUMNS
    assert [r["date"] for r in result] == [
        "2026-10-03",
        "2026-10-02",
        "2026-09-12",
        "2026-09-12",
        "2026-09-05",
        "2026-09-05",
    ]
    milk = next(r for r in result if r["item"] == "Milk")
    assert milk == {
        "date": "2026-09-05",
        "store": "FreshCo",
        "item": "Milk",
        "category": "Groceries",
        "subcategory": "",
        "quantity": "1",
        "unit_price": "5.49",
        "amount": "5.49",
        "currency": "CAD",
        "receipt_total": "4.49",
        "receipt_id": RECEIPTS[0].receipt_id,
    }


def test_negative_amounts_stay_numbers() -> None:
    coupon = next(r for r in rows(export_csv(RECEIPTS, "items")) if r["item"] == "Coupon")

    assert coupon["amount"] == "-1.00"
    assert coupon["unit_price"] == "-1.00"


def test_not_itemized_row_keeps_amounts_adding_up() -> None:
    ramen_rows = [r for r in rows(export_csv(RECEIPTS, "items")) if r["store"] == "Ramen Isshin"]

    assert [(r["category"], r["amount"]) for r in ramen_rows] == [
        ("Dining Out", "18"),
        ("(not itemized)", "2.34"),
    ]
    assert sum(Decimal(r["amount"]) for r in ramen_rows) == Decimal("20.34")


def test_formula_like_text_is_neutralized() -> None:
    row = next(r for r in rows(export_csv(RECEIPTS, "items")) if r["date"] == "2026-10-03")

    assert row["store"] == '\'=HYPERLINK("http://evil")'
    assert row["item"] == "'@SUM(A1)"


def test_japanese_text_survives() -> None:
    row = next(r for r in rows(export_csv(RECEIPTS, "items")) if r["currency"] == "JPY")

    assert (row["store"], row["item"], row["amount"]) == ("セブン-イレブン", "おにぎり", "150")


def test_items_export_with_category_keeps_only_that_category() -> None:
    result = rows(export_csv(RECEIPTS, "items", ReceiptFilter(category="groceries")))

    assert {r["category"] for r in result} == {"Groceries"}
    assert [r["item"] for r in result] == ["おにぎり", "Milk", "Coupon"]


def test_receipts_export_with_category_keeps_whole_receipts() -> None:
    result = rows(export_csv(RECEIPTS, "receipts", ReceiptFilter(category="Dining Out")))

    assert len(result) == 4  # the endpoint selects receipts; the receipts export doesn't drop any


def test_receipts_export() -> None:
    result = rows(export_csv(RECEIPTS, "receipts"))

    assert list(result[0]) == RECEIPT_COLUMNS
    assert len(result) == 4
    freshco = next(r for r in result if r["store"] == "FreshCo")
    assert (freshco["total"], freshco["items"], freshco["categories"]) == ("4.49", "2", "Groceries")


def test_empty_export_has_header_only() -> None:
    text = export_csv([], "items")

    assert text == UTF8_BOM + ",".join(ITEM_COLUMNS) + "\r\n"


class FakeRepository:
    def __init__(self) -> None:
        self.filters: list[ReceiptFilter] = []

    def list_receipts(self, filters: ReceiptFilter) -> list[Receipt]:
        self.filters.append(filters)
        return RECEIPTS


@pytest.fixture
def repository() -> FakeRepository:
    return FakeRepository()


@pytest.fixture
def client(repository: FakeRepository) -> Iterator[TestClient]:
    app.dependency_overrides[get_settings] = lambda: make_settings()
    app.dependency_overrides[get_repository] = lambda: repository
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_export_endpoint_downloads_csv(client: TestClient) -> None:
    response = client.get("/api/export/csv")

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert response.headers["content-disposition"] == (
        f'attachment; filename="loonietoonie-items-{date.today().isoformat()}.csv"'
    )
    assert response.content.startswith(UTF8_BOM.encode())
    assert len(rows(response.content.decode("utf-8"))) == 6


def test_export_endpoint_passes_filters_and_currency(client: TestClient, repository: FakeRepository) -> None:
    response = client.get(
        "/api/export/csv",
        params={
            "kind": "receipts",
            "from": "2026-09-01",
            "to": "2026-10-31",
            "category": "Groceries",
            "currency": "jpy",
        },
    )

    assert repository.filters == [
        ReceiptFilter(date_from=date(2026, 9, 1), date_to=date(2026, 10, 31), category="Groceries")
    ]
    assert [r["currency"] for r in rows(response.content.decode("utf-8"))] == ["JPY"]
    assert "loonietoonie-receipts-" in response.headers["content-disposition"]


@pytest.mark.parametrize("params", [{"kind": "everything"}, {"currency": "dollars"}, {"from": "last week"}])
def test_export_endpoint_validates(client: TestClient, params: dict[str, str]) -> None:
    assert client.get("/api/export/csv", params=params).status_code == 422


def test_export_requires_sign_in() -> None:
    app.dependency_overrides[get_settings] = lambda: make_settings()
    with TestClient(app) as client:
        assert client.get("/api/export/csv").status_code == 401
    app.dependency_overrides.clear()


def test_items_export_with_subcategory_filter() -> None:
    receipts = [
        receipt("2026-09-05", "Metro", [("Bread", "3.00", "Groceries"), ("Apples", "4.00", "Groceries")])
    ]
    receipts[0].items[0].subcategory = "Bakery"

    result = rows(export_csv(receipts, "items", ReceiptFilter(subcategory="bakery")))

    assert [(r["item"], r["category"], r["subcategory"]) for r in result] == [
        ("Bread", "Groceries", "Bakery")
    ]
