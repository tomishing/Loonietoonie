from datetime import date

import pytest
from pydantic import ValidationError

from app.services.spending_queries import (
    BreakdownInput,
    FindReceiptsInput,
    MonthOverviewInput,
    SpendingQueries,
)
from tests.test_summary import receipt

TODAY = date(2026, 10, 13)

RECEIPTS = [
    receipt(
        "2026-09-05", "FreshCo", [("Milk 2%", "5.49", "Groceries"), ("Eggs", "4.00", "Groceries")]
    ),  # Sat
    receipt("2026-09-07", "FreshCo", [("milk 2%", "5.49", "Groceries")]),  # Mon
    receipt(
        "2026-09-12", "Ramen Isshin", [("Ramen", "18", "Dining Out"), ("HST", "2.34", "Tax & Fees")]
    ),  # Sat
    receipt("2026-10-02", "Metro", [("Bread", "3.99", "Groceries")], total="4.50"),  # Fri, 0.51 not itemized
    receipt("2026-09-20", "セブン", [("おにぎり", "150", "Groceries")], currency="JPY"),
]


@pytest.fixture
def queries() -> SpendingQueries:
    return SpendingQueries(RECEIPTS, default_currency="CAD", today=TODAY)


def breakdown(queries: SpendingQueries, **kwargs: object) -> dict[str, object]:
    values: dict[str, object] = {"date_from": "2026-09-01", "date_to": "2026-10-31", "group_by": "category"}
    values.update(kwargs)
    return queries.spending_breakdown(BreakdownInput.model_validate(values))


def test_breakdown_by_category(queries: SpendingQueries) -> None:
    result = breakdown(queries)

    assert result["currency"] == "CAD"
    assert result["total"] == "39.82"
    assert result["receipts"] == 4
    assert result["rows"] == [
        {"key": "Groceries", "total": "18.97", "receipts": 3},
        {"key": "Dining Out", "total": "18", "receipts": 1},
        {"key": "Tax & Fees", "total": "2.34", "receipts": 1},
        {"key": "(not itemized)", "total": "0.51", "receipts": 1},
    ]


def test_breakdown_by_month_is_chronological(queries: SpendingQueries) -> None:
    rows = breakdown(queries, group_by="month")["rows"]

    assert rows == [
        {"key": "2026-09", "total": "35.32", "receipts": 3},
        {"key": "2026-10", "total": "4.50", "receipts": 1},
    ]


def test_breakdown_by_weekday(queries: SpendingQueries) -> None:
    rows = breakdown(queries, group_by="weekday")["rows"]

    assert [(r["key"], r["total"]) for r in rows] == [
        ("Monday", "5.49"),
        ("Friday", "4.50"),
        ("Saturday", "29.83"),
    ]


def test_breakdown_by_item_merges_spelling_and_counts_times(queries: SpendingQueries) -> None:
    rows = breakdown(queries, group_by="item", category="groceries")["rows"]

    assert rows[0] == {"key": "Milk 2%", "total": "10.98", "times_bought": 2}
    assert all(r["key"] for r in rows)  # "(not itemized)" amounts have no item name


def test_breakdown_filters(queries: SpendingQueries) -> None:
    assert breakdown(queries, group_by="store", store="fresh")["total"] == "14.98"
    assert breakdown(queries, item_contains="MILK")["total"] == "10.98"
    assert breakdown(queries, date_from="2026-10-01")["total"] == "4.50"


def test_breakdown_other_currency_and_limit(queries: SpendingQueries) -> None:
    assert breakdown(queries, currency="jpy")["total"] == "150"

    limited = breakdown(queries, limit=2)
    assert len(limited["rows"]) == 2
    assert limited["rows_omitted"] == 2


def test_breakdown_with_no_matches(queries: SpendingQueries) -> None:
    assert breakdown(queries, store="nowhere") == {"currency": "CAD", "total": "0", "receipts": 0, "rows": []}


def test_find_receipts(queries: SpendingQueries) -> None:
    result = queries.find_receipts(
        FindReceiptsInput.model_validate(
            {"date_from": "2026-09-01", "date_to": "2026-09-30", "item_contains": "milk"}
        )
    )

    assert result["matching_receipts"] == 2
    assert result["total_of_matching_receipts"] == "14.98"
    assert [r["date"] for r in result["receipts"]] == ["2026-09-07", "2026-09-05"]
    assert result["receipts"][1]["items"][0] == {
        "name": "Milk 2%",
        "quantity": "1",
        "unit_price": "5.49",
        "category": "Groceries",
    }


def test_find_receipts_limit(queries: SpendingQueries) -> None:
    result = queries.find_receipts(
        FindReceiptsInput.model_validate({"date_from": "2026-01-01", "date_to": "2026-12-31", "limit": 1})
    )

    assert len(result["receipts"]) == 1
    assert result["receipts_omitted"] == 3


def test_month_overview_matches_dashboard(queries: SpendingQueries) -> None:
    result = queries.month_overview(MonthOverviewInput(month="2026-09"))

    assert result["total"] == "35.32"
    assert result["period"]["month"] == "2026-09"
    assert "trend" not in result


def test_context(queries: SpendingQueries) -> None:
    assert queries.context() == {
        "today": "2026-10-13",
        "default_currency": "CAD",
        "receipts_by_currency": {"CAD": 4, "JPY": 1},
        "first_receipt": "2026-09-05",
        "last_receipt": "2026-10-02",
    }


@pytest.mark.parametrize(
    "values",
    [
        {"date_from": "2026-10-01", "date_to": "2026-09-01", "group_by": "category"},
        {"date_from": "2026-09-01", "date_to": "2026-09-30", "group_by": "colour"},
        {"date_from": "2026-09-01", "date_to": "2026-09-30", "group_by": "store", "limit": 500},
        {"date_from": "yesterday", "date_to": "2026-09-30", "group_by": "store"},
    ],
)
def test_invalid_inputs_are_rejected(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        BreakdownInput.model_validate(values)
