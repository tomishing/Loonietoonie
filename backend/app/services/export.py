"""CSV export of receipts or items, ready for Excel / Numbers / Google Sheets."""

import csv
import io
from decimal import Decimal
from typing import Literal

from app.services.summary import NOT_ITEMIZED
from app.storage.models import Receipt

ExportKind = Literal["items", "receipts"]

ITEM_COLUMNS = [
    "date",
    "store",
    "item",
    "category",
    "quantity",
    "unit_price",
    "amount",
    "currency",
    "receipt_total",
    "receipt_id",
]
RECEIPT_COLUMNS = ["date", "store", "total", "currency", "items", "categories", "photo", "receipt_id"]

# Cells starting with these are treated as formulas by spreadsheet apps (CSV injection).
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
UTF8_BOM = "﻿"  # lets Excel open UTF-8 (Japanese, French accents) correctly


def safe_text(value: str) -> str:
    """Neutralize text that a spreadsheet would run as a formula. Used for free-text columns only."""
    return "'" + value if value.startswith(FORMULA_PREFIXES) else value


def _money(value: Decimal) -> str:
    return format(value, "f")


def export_csv(receipts: list[Receipt], kind: ExportKind, category: str | None = None) -> str:
    """Receipts newest first. Amounts are plain decimals; text columns are injection-safe.

    With `category`, the items export keeps only that category's items (so the file is
    "my Dining Out items"); the receipts export lists every receipt that has such an item.
    """
    only = category.strip().casefold() if category else None
    buffer = io.StringIO()
    buffer.write(UTF8_BOM)
    writer = csv.writer(buffer, lineterminator="\r\n")
    ordered = sorted(receipts, key=lambda r: (r.purchased_at, r.created_at), reverse=True)

    if kind == "receipts":
        writer.writerow(RECEIPT_COLUMNS)
        for receipt in ordered:
            categories = sorted({item.category for item in receipt.items})
            writer.writerow(
                [
                    receipt.purchased_at.isoformat(),
                    safe_text(receipt.store),
                    _money(receipt.total),
                    receipt.currency,
                    len(receipt.items),
                    safe_text("; ".join(categories)),
                    receipt.image_link,
                    receipt.receipt_id,
                ]
            )
        return buffer.getvalue()

    writer.writerow(ITEM_COLUMNS)
    for receipt in ordered:
        common_tail = [receipt.currency, _money(receipt.total), receipt.receipt_id]
        itemized = Decimal(0)
        for item in receipt.items:
            amount = item.quantity * item.price
            itemized += amount
            if only is not None and item.category.casefold() != only:
                continue
            writer.writerow(
                [
                    receipt.purchased_at.isoformat(),
                    safe_text(receipt.store),
                    safe_text(item.name),
                    safe_text(item.category),
                    _money(item.quantity),
                    _money(item.price),
                    _money(amount),
                    *common_tail,
                ]
            )
        if receipt.total != itemized and only is None:
            # Keeps the amount column adding up to the receipt totals.
            remainder = _money(receipt.total - itemized)
            writer.writerow(
                [
                    receipt.purchased_at.isoformat(),
                    safe_text(receipt.store),
                    "",
                    NOT_ITEMIZED,
                    "1",
                    remainder,
                    remainder,
                    *common_tail,
                ]
            )
    return buffer.getvalue()
