import uuid
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.services.summary import NOT_ITEMIZED, OTHER, shift_month, summarize
from app.storage.dependencies import get_repository
from app.storage.models import Item, Receipt, ReceiptFilter
from tests.test_auth import make_settings

TODAY = date(2026, 10, 13)
D = Decimal


def receipt(
    day: str, store: str, items: list[tuple[str, str, str]], total: str | None = None, currency: str = "CAD"
) -> Receipt:
    """items: (name, line amount, category); quantity 1."""
    parsed = [
        Item(item_id=str(uuid.uuid4()), name=name, quantity=D(1), price=D(amount), category=category)
        for name, amount, category in items
    ]
    return Receipt(
        receipt_id=str(uuid.uuid4()),
        store=store,
        purchased_at=date.fromisoformat(day),
        total=D(total) if total is not None else sum((i.price for i in parsed), D(0)),
        currency=currency,
        image_link="",
        created_at="",
        updated_at="",
        items=parsed,
    )


def run(receipts: list[Receipt], month: str = "2026-09", currency: str = "CAD", today: date = TODAY) -> Any:
    return summarize(receipts, month=month, currency=currency, default_currency="CAD", today=today)


def test_shift_month() -> None:
    assert shift_month("2026-01", -1) == "2025-12"
    assert shift_month("2026-10", -11) == "2025-11"
    assert shift_month("2025-12", 1) == "2026-01"


def test_totals_compare_with_previous_month_and_average_of_three() -> None:
    receipts = [
        receipt("2026-05-10", "A", [("x", "50", "Groceries")]),  # too old for the average
        receipt("2026-06-10", "A", [("x", "100", "Groceries")]),
        receipt("2026-07-10", "A", [("x", "200", "Groceries")]),
        receipt("2026-08-10", "A", [("x", "300", "Groceries")]),
        receipt("2026-09-10", "A", [("x", "0.1", "Groceries")]),
        receipt("2026-09-20", "B", [("y", "0.2", "Household")]),
    ]

    summary = run(receipts)

    assert summary.total == D("0.3")  # exact, no float drift
    assert summary.receipt_count == 2
    assert summary.previous_total == D(300)
    assert summary.average_total == D(200)
    assert summary.period.partial is False
    assert (summary.period.start, summary.period.end) == (date(2026, 9, 1), date(2026, 9, 30))


def test_averages_are_rounded_to_cents() -> None:
    receipts = [
        receipt("2026-06-10", "A", [("x", "10", "Groceries")]),
        receipt("2026-07-10", "A", [("x", "10", "Groceries")]),
        receipt("2026-08-10", "A", [("x", "0.01", "Groceries")]),
        receipt("2026-09-10", "A", [("x", "1", "Groceries")]),
    ]

    summary = run(receipts)

    assert summary.average_total == D("6.67")
    assert summary.categories[0].average_total == D("6.67")


def test_average_only_counts_months_since_first_receipt() -> None:
    receipts = [
        receipt("2026-08-10", "A", [("x", "90", "Groceries")]),
        receipt("2026-09-10", "A", [("x", "10", "Groceries")]),
    ]

    assert run(receipts).average_total == D(90)


def test_no_history_means_no_average_and_no_changes() -> None:
    summary = run([receipt("2026-09-10", "A", [("x", "10", "Groceries")])])

    assert summary.average_total is None
    assert summary.categories[0].average_total is None
    assert summary.changes == []


def test_month_in_progress_compares_same_days() -> None:
    receipts = [
        receipt("2026-09-05", "A", [("x", "40", "Groceries")]),
        receipt("2026-09-25", "A", [("x", "500", "Groceries")]),  # after day 13: not compared
        receipt("2026-10-02", "A", [("x", "30", "Groceries")]),
    ]

    summary = run(receipts, month="2026-10")

    assert summary.period.partial is True
    assert summary.period.end == date(2026, 10, 13)
    assert summary.total == D(30)
    assert summary.previous_total == D(40)


def test_currencies_are_summarized_separately() -> None:
    receipts = [
        receipt("2026-09-10", "A", [("x", "10", "Groceries")]),
        receipt("2026-09-11", "セブン", [("おにぎり", "150", "Groceries")], currency="JPY"),
        receipt("2026-09-12", "Diner", [("pancakes", "9.95", "Dining Out")], currency="USD"),
    ]

    cad = run(receipts)
    jpy = run(receipts, currency="JPY")

    assert cad.total == D(10)
    assert jpy.total == D(150)
    assert cad.currencies == ["CAD", "JPY", "USD"]


def test_default_currency_is_listed_even_without_receipts() -> None:
    summary = run([receipt("2026-09-11", "S", [("x", "150", "Groceries")], currency="JPY")], currency="JPY")

    assert summary.currencies == ["CAD", "JPY"]


def test_categories_sorted_with_shares_and_zero_this_month() -> None:
    receipts = [
        receipt("2026-08-10", "A", [("toy", "20", "Kids")]),
        receipt("2026-09-10", "A", [("milk", "30", "Groceries"), ("soap", "10", "Household")]),
    ]

    categories = {c.category: c for c in run(receipts).categories}

    assert [c for c in categories] == ["Groceries", "Household", "Kids"]
    assert categories["Groceries"].share == D("0.75")
    assert categories["Kids"].total == 0
    assert categories["Kids"].average_total == D(20)


def test_total_not_covered_by_items_is_shown_as_not_itemized() -> None:
    summary = run([receipt("2026-09-10", "A", [("milk", "10", "Groceries")], total="11.30")])

    categories = {c.category: c.total for c in summary.categories}
    assert categories == {"Groceries": D(10), NOT_ITEMIZED: D("1.30")}
    assert sum(categories.values()) == summary.total


def test_trend_has_twelve_months_top_five_and_other() -> None:
    names = ["Groceries", "Dining Out", "Household", "Health", "Kids", "Pets", "Clothing"]
    receipts = [
        receipt("2026-09-10", "A", [(n, str(100 - i * 10), n) for i, n in enumerate(names)]),
        receipt("2025-10-05", "A", [("old", "5", "Groceries")]),  # 12th month back
        receipt("2025-09-05", "A", [("too old", "999", "Groceries")]),  # outside the window
    ]

    summary = run(receipts)

    assert [t.month for t in summary.trend][0] == "2025-10"
    assert [t.month for t in summary.trend][-1] == "2026-09"
    assert summary.trend_categories == ["Groceries", "Dining Out", "Household", "Health", "Kids", OTHER]
    september = summary.trend[-1]
    assert september.by_category[OTHER] == D(50 + 40)  # Pets + Clothing
    assert september.total == sum(september.by_category.values())
    assert summary.trend[0].by_category["Groceries"] == D(5)
    assert all(set(t.by_category) == set(summary.trend_categories) for t in summary.trend)


def test_real_other_category_does_not_collide_with_folded_categories() -> None:
    names = ["Other", "Groceries", "Dining Out", "Household", "Health", "Kids", "Pets"]
    summary = run([receipt("2026-09-10", "A", [(n, str(100 - i * 10), n) for i, n in enumerate(names)])])

    assert summary.trend_categories == ["Other", "Groceries", "Dining Out", "Household", "Health", OTHER]
    assert summary.trend[-1].by_category["Other"] == D(100)
    assert summary.trend[-1].by_category[OTHER] == D(50 + 40)  # Kids + Pets


def test_trend_without_other_when_few_categories() -> None:
    summary = run([receipt("2026-09-10", "A", [("milk", "10", "Groceries")])])

    assert summary.trend_categories == ["Groceries"]


def test_top_stores_and_items() -> None:
    receipts = [
        receipt("2026-09-01", "FreshCo", [("Milk", "5", "Groceries"), ("HST", "0.65", "Tax & Fees")]),
        receipt("2026-09-08", "FreshCo", [("milk", "5", "Groceries")]),
        receipt("2026-09-09", "Pharmaprix", [("Vitamins", "30", "Health")]),
    ]

    summary = run(receipts)

    assert [(s.store, s.total, s.visits) for s in summary.top_stores] == [
        ("Pharmaprix", D(30), 1),
        ("FreshCo", D("10.65"), 2),
    ]
    assert [(i.name, i.times, i.total) for i in summary.top_items] == [
        ("Milk", 2, D(10)),
        ("Vitamins", 1, D(30)),
    ]


def history(amount: str, category: str = "Dining Out") -> list[Receipt]:
    return [
        receipt(f"2026-0{m}-10", "Usual", [("a", amount, category), ("b", "100", "Groceries")])
        for m in (6, 7, 8)
    ]


def test_category_up_and_down_changes() -> None:
    receipts = [
        *history("50"),
        receipt("2026-09-10", "Usual", [("a", "120", "Dining Out"), ("b", "40", "Groceries")]),
    ]

    changes = [(c.kind, c.category, c.amount, c.baseline) for c in run(receipts).changes]

    assert ("category_up", "Dining Out", D(120), D(50)) in changes
    assert ("category_down", "Groceries", D(40), D(100)) in changes


def test_small_changes_are_ignored() -> None:
    receipts = [
        *history("50"),
        receipt("2026-09-10", "Usual", [("a", "55", "Dining Out"), ("b", "100", "Groceries")]),
    ]

    assert run(receipts).changes == []


def test_new_category_and_new_store() -> None:
    receipts = [
        *history("50"),
        receipt("2026-09-10", "Usual", [("a", "50", "Dining Out"), ("b", "100", "Groceries")]),
        receipt("2026-09-12", "PetSmart", [("food", "40", "Pets")]),
    ]

    changes = [(c.kind, c.category or c.store, c.amount) for c in run(receipts).changes]

    assert ("category_new", "Pets", D(40)) in changes
    assert ("new_store", "PetSmart", D(40)) in changes
    assert not any(store == "Usual" for _, store, _ in changes)


def test_tax_changes_are_not_reported() -> None:
    receipts = [
        *history("50", "Tax & Fees"),
        receipt("2026-09-10", "Usual", [("a", "5", "Tax & Fees"), ("b", "100", "Groceries")]),
    ]

    assert run(receipts).changes == []


def test_empty_data() -> None:
    summary = run([])

    assert summary.total == 0
    assert summary.categories == []
    assert len(summary.trend) == 12
    assert summary.trend_categories == []
    assert summary.top_stores == []


class FakeRepository:
    def __init__(self, receipts: list[Receipt]) -> None:
        self.receipts = receipts

    def list_receipts(self, filters: ReceiptFilter) -> list[Receipt]:
        return self.receipts


@pytest.fixture
def client() -> Iterator[TestClient]:
    repository = FakeRepository([receipt("2026-09-10", "FreshCo", [("milk", "4.99", "Groceries")])])
    app.dependency_overrides[get_settings] = lambda: make_settings()
    app.dependency_overrides[get_repository] = lambda: repository
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_summary_endpoint_returns_strings(client: TestClient) -> None:
    response = client.get("/api/summary", params={"month": "2026-09", "currency": "cad"})

    assert response.status_code == 200
    body = response.json()
    assert body["currency"] == "CAD"
    assert body["total"] == "4.99"
    assert body["categories"][0] == {
        "category": "Groceries",
        "total": "4.99",
        "previous_total": "0",
        "average_total": None,
        "share": "1.0000",
        "subcategories": [],
    }


def test_summary_endpoint_defaults_to_this_month(client: TestClient) -> None:
    body = client.get("/api/summary").json()

    assert body["period"]["month"] == date.today().strftime("%Y-%m")
    assert body["currency"] == "CAD"


@pytest.mark.parametrize("params", [{"month": "2026-13"}, {"month": "Sept"}, {"currency": "dollars"}])
def test_summary_endpoint_validates_params(client: TestClient, params: dict[str, str]) -> None:
    assert client.get("/api/summary", params=params).status_code == 422


def test_summary_requires_sign_in() -> None:
    app.dependency_overrides[get_settings] = lambda: make_settings()
    with TestClient(app) as client:
        assert client.get("/api/summary").status_code == 401
    app.dependency_overrides.clear()


def with_subcategories(r: Receipt, *subcategories: str) -> Receipt:
    for item, subcategory in zip(r.items, subcategories, strict=True):
        item.subcategory = subcategory
    return r


def test_category_drill_down_by_subcategory() -> None:
    receipts = [
        # usual months (Jun–Aug): bakery 10 a month, produce 20 a month
        *(
            with_subcategories(
                receipt(
                    f"2026-0{m}-03", "Metro", [("Bread", "10", "Groceries"), ("Apples", "20", "Groceries")]
                ),
                "Bakery",
                "Produce",
            )
            for m in (6, 7, 8)
        ),
        with_subcategories(
            receipt(
                "2026-09-04",
                "Metro",
                [("Bread", "25", "Groceries"), ("Apples", "5", "Groceries"), ("Gum", "2", "Groceries")],
            ),
            "Bakery",
            "Produce",
            "",
        ),
        receipt("2026-09-05", "Cineplex", [("Movie", "15", "Entertainment")]),
    ]

    categories = {c.category: c for c in run(receipts).categories}

    assert [(s.subcategory, s.total, s.average_total) for s in categories["Groceries"].subcategories] == [
        ("Bakery", D(25), D("10.00")),
        ("Produce", D(5), D("20.00")),
        ("", D(2), D("0.00")),  # items without a subcategory come last
    ]
    assert categories["Entertainment"].subcategories == []  # nothing subcategorized
