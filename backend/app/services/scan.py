"""Scan flow: OCR text → Claude → normalized draft + validation issues.

The draft is not saved here. The user reviews it, then saves it with POST /api/receipts.
If a blocking issue is found and no photo was sent, the response asks the client to retry
once with the photo (`needs_image`), so the photo only leaves the device when needed.
"""

import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel

from app.ai.receipt_parser import ParsedReceipt, ReceiptImage, ReceiptParser
from app.storage.models import Category
from app.storage.repository import Repository

# Amounts may differ from the total by this many of the currency's smallest unit (rounding).
TOLERANCE_UNITS = 2
ZERO_DECIMAL_CURRENCIES = frozenset({"JPY", "KRW", "VND", "CLP", "ISK", "PYG", "UGX", "XAF", "XOF"})
THREE_DECIMAL_CURRENCIES = frozenset({"BHD", "JOD", "KWD", "OMR", "TND"})
MAX_DAYS_IN_FUTURE = 1
OLD_RECEIPT_YEARS = 10

# Issues that make Claude's reading untrustworthy: retry with the photo if possible.
BLOCKING_ISSUES = frozenset(
    {
        "no_text",
        "no_items",
        "total_unreadable",
        "total_mismatch",
        "date_missing",
        "date_future",
        "item_unreadable",
    }
)


class ScanItem(BaseModel):
    name: str
    quantity: Decimal | None
    price: Decimal | None  # unit price
    category: str
    subcategory: str | None = None


class ScanDraft(BaseModel):
    store: str
    purchased_at: date | None
    currency: str
    total: Decimal | None
    items: list[ScanItem]


class ScanIssue(BaseModel):
    code: str
    message: str


class ScanResult(BaseModel):
    draft: ScanDraft
    issues: list[ScanIssue]
    needs_image: bool
    used_image: bool


def tolerance(currency: str) -> Decimal:
    if currency in ZERO_DECIMAL_CURRENCIES:
        unit = Decimal(1)
    elif currency in THREE_DECIMAL_CURRENCIES:
        unit = Decimal("0.001")
    else:
        unit = Decimal("0.01")
    return unit * TOLERANCE_UNITS


_CURRENCY_MARKS = re.compile(r"[\s$¥￥€£円]|CAD|USD|JPY|EUR", re.IGNORECASE)


def parse_money(text: str) -> Decimal | None:
    """Reads "4.99", "-1.00", "1,234.50", "4,99" (French), "¥1,080", "100-" (trailing minus)."""
    cleaned = _CURRENCY_MARKS.sub("", text).replace("−", "-")
    negative = cleaned.startswith("-") or cleaned.endswith("-")
    cleaned = cleaned.strip("-")
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(",", "")
    elif re.fullmatch(r"\d+,\d{1,2}", cleaned):
        cleaned = cleaned.replace(",", ".")
    else:
        cleaned = cleaned.replace(",", "")
    if not re.fullmatch(r"\d+(\.\d{1,3})?|\.\d{1,3}", cleaned):
        return None
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    return -value if negative else value


def _fmt(value: Decimal) -> str:
    return format(value, "f")


def normalize(
    parsed: ParsedReceipt, categories: list[Category], default_currency: str
) -> tuple[ScanDraft, list[ScanIssue]]:
    """Convert Claude's strings into typed values, noting anything that couldn't be read."""
    issues: list[ScanIssue] = []

    currency = (parsed.currency or "").strip().upper()
    if not (len(currency) == 3 and currency.isascii() and currency.isalpha()):
        currency = default_currency
        issues.append(
            ScanIssue(
                code="currency_defaulted", message=f"No currency found on the receipt; using {currency}."
            )
        )

    purchased_at: date | None = None
    if parsed.purchased_at:
        try:
            purchased_at = date.fromisoformat(parsed.purchased_at.strip())
        except ValueError:
            purchased_at = None

    by_name = {c.name.casefold(): c.name for c in categories}
    fallback = by_name.get("other", categories[0].name)
    # A subcategory only counts if it belongs to the item's (final) category.
    subcategories = {c.name: {s.casefold(): s for s in c.subcategories} for c in categories}
    items: list[ScanItem] = []
    for index, item in enumerate(parsed.items):
        quantity = parse_money(item.quantity) if item.quantity.strip() else Decimal(1)
        price = parse_money(item.unit_price)
        if quantity is None or quantity <= 0 or price is None:
            issues.append(
                ScanIssue(
                    code="item_unreadable",
                    message=f"Couldn't read the quantity or price of item {index + 1} ({item.name}).",
                )
            )
        category = by_name.get(item.category.strip().casefold(), fallback)
        subcategory = subcategories.get(category, {}).get((item.subcategory or "").strip().casefold())
        items.append(
            ScanItem(
                name=item.name.strip(),
                quantity=quantity if quantity is not None and quantity > 0 else None,
                price=price,
                category=category,
                subcategory=subcategory,
            )
        )

    total = parse_money(parsed.total)
    draft = ScanDraft(
        store=parsed.store.strip(), purchased_at=purchased_at, currency=currency, total=total, items=items
    )
    return draft, issues


def validate(draft: ScanDraft, today: date) -> list[ScanIssue]:
    """The spec §5 checks: items add up to the total, the date is valid, there is something to save."""
    issues: list[ScanIssue] = []

    if not draft.store:
        issues.append(ScanIssue(code="store_missing", message="Couldn't find the store name."))

    if draft.purchased_at is None:
        issues.append(ScanIssue(code="date_missing", message="Couldn't find the purchase date."))
    elif draft.purchased_at > today + timedelta(days=MAX_DAYS_IN_FUTURE):
        issues.append(ScanIssue(code="date_future", message="The purchase date is in the future."))
    elif draft.purchased_at < today - timedelta(days=365 * OLD_RECEIPT_YEARS):
        issues.append(ScanIssue(code="date_old", message="The purchase date is more than 10 years ago."))

    if not draft.items:
        issues.append(ScanIssue(code="no_items", message="Couldn't find any items."))

    if draft.total is None:
        issues.append(ScanIssue(code="total_unreadable", message="Couldn't read the total."))
    elif draft.items and all(item.quantity is not None and item.price is not None for item in draft.items):
        items_sum = sum(
            (item.quantity * item.price for item in draft.items if item.quantity and item.price is not None),
            Decimal(0),
        )
        if abs(items_sum - draft.total) > tolerance(draft.currency):
            issues.append(
                ScanIssue(
                    code="total_mismatch",
                    message=(
                        f"The items add up to {_fmt(items_sum)} but the total is {_fmt(draft.total)}. "
                        "Check for a missing or misread line."
                    ),
                )
            )
    return issues


def scan_receipt(
    parser: ReceiptParser,
    repository: Repository,
    ocr_text: str,
    image: ReceiptImage | None,
    default_currency: str,
    today: date,
) -> ScanResult:
    if not ocr_text.strip() and image is None:
        empty = ScanDraft(store="", purchased_at=None, currency=default_currency, total=None, items=[])
        issue = ScanIssue(code="no_text", message="No text was recognized in the photo.")
        return ScanResult(draft=empty, issues=[issue], needs_image=True, used_image=False)

    categories = repository.list_categories() or [Category(name="Other", icon="")]
    parsed = parser.parse(ocr_text, categories, default_currency, today, image)
    draft, issues = normalize(parsed, categories, default_currency)
    issues += validate(draft, today)
    blocking = any(issue.code in BLOCKING_ISSUES for issue in issues)
    return ScanResult(
        draft=draft, issues=issues, needs_image=blocking and image is None, used_image=image is not None
    )
