from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.sheets.setup import ensure_spreadsheet
from app.storage.dependencies import get_repository
from app.storage.sheets_repository import SheetsRepository, TabReadCache
from tests.fakes import FakeSheetsApi
from tests.test_auth import make_settings


@pytest.fixture
def client() -> Iterator[TestClient]:
    api = FakeSheetsApi()
    spreadsheet_id = ensure_spreadsheet(api, "2026-10-13T00:00:00Z")
    repository = SheetsRepository(api, spreadsheet_id, TabReadCache())
    app.dependency_overrides[get_settings] = lambda: make_settings(default_currency="JPY")
    app.dependency_overrides[get_repository] = lambda: repository
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def body(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "store": "FreshMart",
        "purchased_at": "2026-10-01",
        "total": "7.47",
        "currency": "usd",
        "items": [
            {"name": "Milk", "quantity": "1", "price": "3.49", "category": "Groceries"},
            {"name": "Soap", "quantity": "2", "price": "1.99", "category": "household"},
        ],
    }
    data.update(overrides)
    return data


def errors(response: Any) -> list[str]:
    return [".".join(str(part) for part in e["loc"]) for e in response.json()["detail"]]


def test_create_and_get_receipt(client: TestClient) -> None:
    created = client.post("/api/receipts", json=body())

    assert created.status_code == 201
    receipt = created.json()
    assert receipt["currency"] == "USD"
    assert receipt["total"] == "7.47"
    assert receipt["items"][1]["category"] == "Household"  # canonical name from the categories tab
    assert receipt["items"][1]["quantity"] == "2"
    assert client.get(f"/api/receipts/{receipt['receipt_id']}").json() == receipt


def test_currency_defaults_to_setting(client: TestClient) -> None:
    data = body()
    del data["currency"]

    assert client.post("/api/receipts", json=data).json()["currency"] == "JPY"


def test_numbers_are_accepted_and_returned_as_strings(client: TestClient) -> None:
    response = client.post(
        "/api/receipts",
        json=body(total=10.5, items=[{"name": "Tea", "price": 10.5, "category": "Groceries"}]),
    )

    assert response.status_code == 201
    assert response.json()["total"] == "10.5"
    assert response.json()["items"][0]["quantity"] == "1"


def test_list_receipts_with_filters(client: TestClient) -> None:
    client.post("/api/receipts", json=body(purchased_at="2026-09-10"))
    october = client.post(
        "/api/receipts",
        json=body(
            purchased_at="2026-10-02",
            total="12",
            items=[{"name": "Ramen", "price": "12", "category": "Dining Out"}],
        ),
    ).json()

    assert len(client.get("/api/receipts").json()) == 2
    assert [r["receipt_id"] for r in client.get("/api/receipts", params={"from": "2026-10-01"}).json()] == [
        october["receipt_id"]
    ]
    assert [r["store"] for r in client.get("/api/receipts", params={"category": "Groceries"}).json()] == [
        "FreshMart"
    ]


def test_unknown_category_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/receipts", json=body(items=[{"name": "Lotto", "price": "2", "category": "Gambling"}])
    )

    assert response.status_code == 422
    assert errors(response) == ["body.items.0.category"]


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"total": "-1"}, "body.total"),
        ({"total": "1.2345"}, "body.total"),
        ({"total": "NaN"}, "body.total"),
        ({"currency": "dollars"}, "body.currency"),
        ({"store": "   "}, "body.store"),
        ({"purchased_at": "2026-02-30"}, "body.purchased_at"),
        ({"items": []}, "body.items"),
        (
            {"items": [{"name": "Milk", "quantity": "0", "price": "1", "category": "Groceries"}]},
            "body.items.0.quantity",
        ),
    ],
)
def test_invalid_receipts_are_rejected(client: TestClient, overrides: dict[str, Any], field: str) -> None:
    response = client.post("/api/receipts", json=body(**overrides))

    assert response.status_code == 422
    assert field in errors(response)


def test_future_date_is_rejected(client: TestClient) -> None:
    response = client.post("/api/receipts", json=body(purchased_at="2999-01-01"))

    assert response.status_code == 422
    assert errors(response) == ["body.purchased_at"]


def test_negative_item_price_is_allowed_for_discounts(client: TestClient) -> None:
    response = client.post(
        "/api/receipts",
        json=body(
            total="2.49",
            items=[
                {"name": "Milk", "price": "3.49", "category": "Groceries"},
                {"name": "Coupon", "price": "-1.00", "category": "Groceries"},
            ],
        ),
    )

    assert response.status_code == 201


def test_update_receipt(client: TestClient) -> None:
    receipt = client.post("/api/receipts", json=body()).json()
    milk = receipt["items"][0]

    response = client.put(
        f"/api/receipts/{receipt['receipt_id']}",
        json=body(store="Updated", total="3.99", items=[{**milk, "price": "3.99"}]),
    )

    assert response.status_code == 200
    assert response.json()["store"] == "Updated"
    assert [(i["item_id"], i["price"]) for i in response.json()["items"]] == [(milk["item_id"], "3.99")]


def test_update_rejects_item_from_another_receipt(client: TestClient) -> None:
    first = client.post("/api/receipts", json=body()).json()
    second = client.post("/api/receipts", json=body(), params={"allow_duplicate": "true"}).json()
    foreign = first["items"][0]

    response = client.put(f"/api/receipts/{second['receipt_id']}", json=body(items=[foreign]))

    assert response.status_code == 422
    assert errors(response) == ["body.items.0.item_id"]


def test_update_rejects_duplicate_item(client: TestClient) -> None:
    receipt = client.post("/api/receipts", json=body()).json()
    milk = receipt["items"][0]

    response = client.put(f"/api/receipts/{receipt['receipt_id']}", json=body(items=[milk, milk]))

    assert response.status_code == 422
    assert errors(response) == ["body.items.1.item_id"]


def test_delete_receipt(client: TestClient) -> None:
    receipt = client.post("/api/receipts", json=body()).json()

    assert client.delete(f"/api/receipts/{receipt['receipt_id']}").status_code == 204
    assert client.get(f"/api/receipts/{receipt['receipt_id']}").status_code == 404
    assert client.get("/api/receipts").json() == []


@pytest.mark.parametrize("method", ["get", "put", "delete"])
def test_missing_receipt_returns_404(client: TestClient, method: str) -> None:
    kwargs: dict[str, Any] = {"json": body()} if method == "put" else {}

    response = client.request(method.upper(), "/api/receipts/does-not-exist", **kwargs)

    assert response.status_code == 404


def test_receipts_require_sign_in() -> None:
    app.dependency_overrides[get_settings] = lambda: make_settings()
    with TestClient(app) as client:
        assert client.get("/api/receipts").status_code == 401
        assert client.post("/api/receipts", json=body()).status_code == 401
    app.dependency_overrides.clear()


def test_subcategory_is_saved_with_the_sheet_spelling(client: TestClient) -> None:
    items = [
        {"name": "Milk", "price": "3.49", "category": "Groceries", "subcategory": "dairy & eggs"},
        {"name": "Soap", "quantity": "2", "price": "1.99", "category": "Household", "subcategory": " "},
    ]
    created = client.post("/api/receipts", json=body(items=items))

    assert created.status_code == 201
    saved = created.json()["items"]
    assert [(i["category"], i["subcategory"]) for i in saved] == [
        ("Groceries", "Dairy & Eggs"),
        ("Household", ""),
    ]
    fetched = client.get(f"/api/receipts/{created.json()['receipt_id']}").json()
    assert fetched["items"][0]["subcategory"] == "Dairy & Eggs"


def test_subcategory_must_belong_to_its_category(client: TestClient) -> None:
    items = [{"name": "Milk", "price": "3.49", "category": "Household", "subcategory": "Dairy & Eggs"}]
    response = client.post("/api/receipts", json=body(items=items, total="3.49"))

    assert response.status_code == 422
    assert errors(response) == ["body.items.0.subcategory"]


def test_list_receipts_by_subcategory(client: TestClient) -> None:
    bakery = [{"name": "Bread", "price": "3.00", "category": "Groceries", "subcategory": "Bakery"}]
    fruit = "Fresh Fruit & Vegetables"
    produce = [{"name": "Apples", "price": "4.00", "category": "Groceries", "subcategory": fruit}]
    client.post("/api/receipts", json=body(items=bakery, total="3.00"))
    client.post("/api/receipts", json=body(items=produce, total="4.00"))

    response = client.get("/api/receipts", params={"category": "Groceries", "subcategory": "bakery"})

    assert [r["items"][0]["name"] for r in response.json()] == ["Bread"]


def test_edit_keeps_a_subcategory_that_was_renamed_since() -> None:
    api = FakeSheetsApi()
    spreadsheet_id = ensure_spreadsheet(api, "2026-10-13T00:00:00Z")
    app.dependency_overrides[get_settings] = lambda: make_settings()
    app.dependency_overrides[get_repository] = lambda: SheetsRepository(
        api, spreadsheet_id, TabReadCache(ttl_seconds=0)
    )
    with TestClient(app) as client:
        items = [{"name": "Milk", "price": "3.49", "category": "Groceries", "subcategory": "Dairy & Eggs"}]
        created = client.post("/api/receipts", json=body(items=items, total="3.49")).json()
        # The user renames the subcategory in the spreadsheet.
        for row in api.sheet(spreadsheet_id, "subcategories").rows:
            if row[1] == "Dairy & Eggs":
                row[1] = "Dairy"
        url = f"/api/receipts/{created['receipt_id']}"
        item = created["items"][0]

        kept = client.put(url, json=body(items=[{**item, "name": "Milk 2%"}], total="3.49"))
        new_item = client.put(url, json=body(items=[{**item, "item_id": None}], total="3.49"))
    app.dependency_overrides.clear()

    assert kept.status_code == 200
    assert kept.json()["items"][0]["subcategory"] == "Dairy & Eggs"
    assert new_item.status_code == 422  # a new item must use a listed subcategory


def test_duplicate_receipt_needs_confirmation(client: TestClient) -> None:
    first = client.post("/api/receipts", json=body()).json()

    again = client.post("/api/receipts", json=body(store="FRESHMART #12"))  # same store, date, total
    assert again.status_code == 409
    assert again.json()["detail"]["duplicate"] == {
        "receipt_id": first["receipt_id"],
        "store": "FreshMart",
        "purchased_at": "2026-10-01",
        "total": "7.47",
        "currency": "USD",
    }
    other_day = client.post("/api/receipts", json=body(purchased_at="2026-10-02"))
    assert other_day.status_code == 201
    anyway = client.post("/api/receipts", json=body(), params={"allow_duplicate": "true"})
    assert anyway.status_code == 201
    assert len(client.get("/api/receipts").json()) == 3
