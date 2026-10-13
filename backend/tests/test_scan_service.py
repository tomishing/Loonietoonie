from datetime import date
from decimal import Decimal

import pytest

from app.ai.receipt_parser import ParsedItem, ParsedReceipt, ReceiptImage
from app.services.scan import normalize, parse_money, scan_receipt, tolerance, validate
from app.storage.models import Category

TODAY = date(2026, 10, 13)
CATEGORIES = ["Groceries", "Dining Out", "Household", "Health", "Personal Care", "Tax & Fees", "Other"]


class FakeRepository:
    def list_categories(self) -> list[Category]:
        return [Category(name=name, icon="") for name in CATEGORIES]


class FakeParser:
    def __init__(self, *responses: ParsedReceipt) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, ReceiptImage | None]] = []

    def parse(
        self,
        ocr_text: str,
        categories: list[str],
        default_currency: str,
        today: date,
        image: ReceiptImage | None = None,
    ) -> ParsedReceipt:
        self.calls.append((ocr_text, image))
        return self.responses.pop(0)


def item(name: str, price: str, category: str = "Groceries", quantity: str = "1") -> ParsedItem:
    return ParsedItem(name=name, quantity=quantity, unit_price=price, category=category)


def receipt(total: str, items: list[ParsedItem], **overrides: str | None) -> ParsedReceipt:
    values: dict[str, object] = {"store": "FreshCo", "purchased_at": "2026-10-05", "currency": "CAD"}
    values.update(overrides)
    return ParsedReceipt(total=total, items=items, **values)  # type: ignore[arg-type]


ONTARIO = receipt(
    "12.49",
    [
        item("MILK 2% 4L", "6.49"),
        item("BANANAS 0.854 kg", "1.49"),
        item("DISH SOAP", "4.99", "Household"),
        item("COUPON DISH SOAP", "-1.00", "Household"),
        item("HST 13%", "0.52", "Tax & Fees"),
    ],
)
IMAGE = ReceiptImage(data=b"jpeg-bytes", media_type="image/jpeg")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("4.99", "4.99"),
        ("-1.00", "-1.00"),
        ("1,234.50", "1234.50"),
        ("4,99", "4.99"),
        ("¥1,080", "1080"),
        ("1080円", "1080"),
        ("$ 12.49", "12.49"),
        ("100-", "-100"),
        ("−2.00", "-2.00"),
        ("12.49 CAD", "12.49"),
        (".5", "0.5"),
    ],
)
def test_parse_money(text: str, expected: str) -> None:
    assert parse_money(text) == Decimal(expected)


@pytest.mark.parametrize("text", ["", "abc", "1.2345", "1e3", "NaN", "--", "4.9.9"])
def test_parse_money_rejects(text: str) -> None:
    assert parse_money(text) is None


def test_tolerance_is_two_smallest_units() -> None:
    assert tolerance("CAD") == Decimal("0.02")
    assert tolerance("JPY") == Decimal(2)
    assert tolerance("KWD") == Decimal("0.002")


def test_valid_receipt_has_no_issues_and_no_retry() -> None:
    parser = FakeParser(ONTARIO)

    result = scan_receipt(parser, FakeRepository(), "ocr text", None, "CAD", TODAY)

    assert result.issues == []
    assert result.needs_image is False
    assert result.used_image is False
    assert result.draft.total == Decimal("12.49")
    assert result.draft.purchased_at == date(2026, 10, 5)
    assert [i.category for i in result.draft.items][-1] == "Tax & Fees"
    assert parser.calls == [("ocr text", None)]


def test_total_within_two_cents_is_accepted() -> None:
    parsed = receipt("10.02", [item("A", "5.00"), item("B", "5.00")])

    assert scan_receipt(FakeParser(parsed), FakeRepository(), "t", None, "CAD", TODAY).issues == []


def test_total_mismatch_asks_for_the_photo() -> None:
    parsed = receipt("10.03", [item("A", "5.00"), item("B", "5.00")])

    result = scan_receipt(FakeParser(parsed), FakeRepository(), "t", None, "CAD", TODAY)

    assert [i.code for i in result.issues] == ["total_mismatch"]
    assert "10.00" in result.issues[0].message
    assert result.needs_image is True


def test_retry_with_photo_never_asks_again() -> None:
    parsed = receipt("10.03", [item("A", "5.00"), item("B", "5.00")])
    parser = FakeParser(parsed)

    result = scan_receipt(parser, FakeRepository(), "t", IMAGE, "CAD", TODAY)

    assert result.needs_image is False
    assert result.used_image is True
    assert [i.code for i in result.issues] == ["total_mismatch"]
    assert parser.calls[0][1] is IMAGE


def test_quantity_times_unit_price_is_used() -> None:
    parsed = receipt("6.98", [item("DENTIFRICE", "3.49", "Personal Care", quantity="2")])

    assert scan_receipt(FakeParser(parsed), FakeRepository(), "t", None, "CAD", TODAY).issues == []


def test_japanese_yen_tolerance() -> None:
    parsed = receipt("1052", [item("弁当", "598"), item("おにぎり", "452")], currency="JPY")

    assert scan_receipt(FakeParser(parsed), FakeRepository(), "t", None, "CAD", TODAY).issues == []


def test_empty_ocr_text_skips_claude_and_asks_for_photo() -> None:
    parser = FakeParser()

    result = scan_receipt(parser, FakeRepository(), "   ", None, "CAD", TODAY)

    assert parser.calls == []
    assert result.needs_image is True
    assert [i.code for i in result.issues] == ["no_text"]
    assert result.draft.currency == "CAD"


def test_empty_ocr_text_with_photo_calls_claude() -> None:
    parser = FakeParser(ONTARIO)

    result = scan_receipt(parser, FakeRepository(), "", IMAGE, "CAD", TODAY)

    assert result.issues == []
    assert len(parser.calls) == 1


def test_missing_currency_uses_default_without_retry() -> None:
    parsed = receipt("5.00", [item("A", "5.00")], currency=None)

    result = scan_receipt(FakeParser(parsed), FakeRepository(), "t", None, "CAD", TODAY)

    assert result.draft.currency == "CAD"
    assert [i.code for i in result.issues] == ["currency_defaulted"]
    assert result.needs_image is False


@pytest.mark.parametrize(
    ("purchased_at", "code", "blocking"),
    [
        (None, "date_missing", True),
        ("not a date", "date_missing", True),
        ("2026-12-01", "date_future", True),
        ("2010-01-01", "date_old", False),
    ],
)
def test_date_checks(purchased_at: str | None, code: str, blocking: bool) -> None:
    parsed = receipt("5.00", [item("A", "5.00")], purchased_at=purchased_at)

    result = scan_receipt(FakeParser(parsed), FakeRepository(), "t", None, "CAD", TODAY)

    assert [i.code for i in result.issues] == [code]
    assert result.needs_image is blocking


def test_unreadable_amounts_are_blocking() -> None:
    parsed = receipt("??", [item("A", "five")])

    result = scan_receipt(FakeParser(parsed), FakeRepository(), "t", None, "CAD", TODAY)

    assert {i.code for i in result.issues} == {"item_unreadable", "total_unreadable"}
    assert result.draft.items[0].price is None
    assert result.needs_image is True


def test_no_items_is_blocking() -> None:
    result = scan_receipt(FakeParser(receipt("5.00", [])), FakeRepository(), "t", None, "CAD", TODAY)

    assert [i.code for i in result.issues] == ["no_items"]
    assert result.needs_image is True


def test_category_names_are_matched_case_insensitively_with_fallback() -> None:
    parsed = receipt("2.00", [item("A", "1.00", "groceries"), item("B", "1.00", "Gadgets")])

    draft, _ = normalize(parsed, CATEGORIES, "CAD")

    assert [i.category for i in draft.items] == ["Groceries", "Other"]


def test_missing_store_is_reported_but_not_blocking() -> None:
    draft, _ = normalize(receipt("1.00", [item("A", "1.00")], store="  "), CATEGORIES, "CAD")

    assert [i.code for i in validate(draft, TODAY)] == ["store_missing"]
