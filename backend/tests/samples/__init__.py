"""Sample receipt OCR texts in several languages, with what a correct reading looks like."""

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

SAMPLES_DIR = Path(__file__).parent


@dataclass(frozen=True)
class Sample:
    file: str
    currency: str
    purchased_at: str
    total: Decimal
    tax_items: int | None  # items expected in "Tax & Fees" (added-on taxes/fees); None = either is fine

    @property
    def text(self) -> str:
        return (SAMPLES_DIR / self.file).read_text(encoding="utf-8")


SAMPLES = [
    Sample("en_ca_grocery.txt", "CAD", "2026-10-05", Decimal("12.49"), tax_items=1),
    Sample("fr_qc_pharmacy.txt", "CAD", "2026-10-08", Decimal("31.35"), tax_items=2),
    Sample("ja_konbini.txt", "JPY", "2026-10-10", Decimal("1051"), tax_items=None),  # bag fee: either way
    Sample("ja_supermarket_tax_added.txt", "JPY", "2026-10-12", Decimal("918"), tax_items=2),
    Sample("us_diner.txt", "USD", "2026-10-11", Decimal("19.26"), tax_items=2),
    # Real Tesseract.js output for the first three (rendered to images): spaced-out Japanese,
    # misread characters (鮭 → "fé"), "\" for ¥, decimal commas.
    Sample("en_ca_grocery.tesseract.txt", "CAD", "2026-10-05", Decimal("12.49"), tax_items=1),
    Sample("fr_qc_pharmacy.tesseract.txt", "CAD", "2026-10-08", Decimal("31.35"), tax_items=2),
    Sample("ja_konbini.tesseract.txt", "JPY", "2026-10-10", Decimal("1051"), tax_items=None),
]
