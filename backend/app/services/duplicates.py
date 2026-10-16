"""Duplicate receipts: the same receipt scanned or typed in twice.

A saved receipt is a likely duplicate of a new one when the purchase date, total, and currency
are the same and the store names match. Saving is not blocked silently: the scan lists it as an
issue on the review screen, and POST /api/receipts answers 409 unless the user chose to save anyway.
"""

import re
import unicodedata
from datetime import date
from decimal import Decimal

from app.services.names import name_words
from app.storage.models import Receipt


def _compact(name: str) -> str:
    """Store name without case, width, spaces, or punctuation (for names without Latin words)."""
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", name).casefold())


def same_store(a: str, b: str) -> bool:
    """FreshCo ↔ "FRESHCO #123", セブン-イレブン ↔ "セブンイレブン"."""
    words_a, words_b = name_words(a), name_words(b)
    if words_a and words_b:
        return bool(words_a & words_b)
    return bool(_compact(a)) and _compact(a) == _compact(b)


def find_duplicates(
    receipts: list[Receipt],
    store: str,
    purchased_at: date,
    total: Decimal,
    currency: str,
    exclude_id: str | None = None,
) -> list[Receipt]:
    """Saved receipts that look like the same purchase, newest first."""
    return [
        r
        for r in receipts
        if r.receipt_id != exclude_id
        and r.purchased_at == purchased_at
        and r.currency == currency.upper()
        and r.total == total
        and same_store(r.store, store)
    ]
