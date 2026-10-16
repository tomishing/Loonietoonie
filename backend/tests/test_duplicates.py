from datetime import date
from decimal import Decimal

import pytest

from app.services.duplicates import find_duplicates, same_store
from tests.test_summary import receipt


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("FreshCo", "FRESHCO #123 TORONTO", True),
        ("Café Olimpico", "CAFE OLIMPICO", True),
        ("セブン-イレブン", "セブンイレブン", True),  # no Latin words: compared without punctuation
        ("ＦｒｅｓｈＣｏ", "freshco", True),  # full-width letters
        ("FreshCo", "Metro", False),
        ("セブン-イレブン", "ローソン", False),
        ("", "", False),
    ],
)
def test_same_store(a: str, b: str, expected: bool) -> None:
    assert same_store(a, b) is expected


def test_find_duplicates_needs_same_date_total_and_currency() -> None:
    saved = receipt("2026-10-05", "FreshCo", [("Milk", "5.49", "Groceries")])
    others = [
        receipt("2026-10-06", "FreshCo", [("Milk", "5.49", "Groceries")]),
        receipt("2026-10-05", "FreshCo", [("Milk", "5.50", "Groceries")]),
        receipt("2026-10-05", "FreshCo", [("Milk", "5.49", "Groceries")], currency="USD"),
    ]

    found = find_duplicates([saved, *others], "FRESHCO 123", date(2026, 10, 5), Decimal("5.49"), "cad")

    assert [r.receipt_id for r in found] == [saved.receipt_id]
    assert (
        find_duplicates([saved], "FreshCo", date(2026, 10, 5), Decimal("5.49"), "CAD", saved.receipt_id) == []
    )
