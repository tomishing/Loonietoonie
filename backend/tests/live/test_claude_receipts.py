"""Calls the real Claude API with the sample receipts. Costs a fraction of a cent per run.

Run with:  RUN_LIVE_CLAUDE=1 .venv/bin/pytest tests/live -v
"""

import os
from datetime import date

import pytest

from app.ai.receipt_parser import ClaudeReceiptParser, claude_client
from app.config import Settings
from app.services.scan import BLOCKING_ISSUES, scan_receipt
from app.sheets.schema import DEFAULT_CATEGORIES, TAX_CATEGORY
from app.storage.models import Category
from tests.samples import SAMPLES, Sample

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_CLAUDE") != "1", reason="set RUN_LIVE_CLAUDE=1 to call Claude"
)

TODAY = date(2026, 10, 13)


class DefaultCategories:
    def list_categories(self) -> list[Category]:
        return [Category(name=name, icon=icon) for name, icon in DEFAULT_CATEGORIES]


@pytest.mark.parametrize("sample", SAMPLES, ids=[s.file for s in SAMPLES])
def test_sample_receipt(sample: Sample) -> None:
    settings = Settings()
    parser = ClaudeReceiptParser(claude_client(settings.anthropic_api_key), settings.anthropic_receipt_model)

    result = scan_receipt(parser, DefaultCategories(), sample.text, None, "CAD", TODAY)

    print(result.model_dump_json(indent=2))
    assert not [i for i in result.issues if i.code in BLOCKING_ISSUES]
    assert result.draft.currency == sample.currency
    assert result.draft.purchased_at is not None
    assert result.draft.purchased_at.isoformat() == sample.purchased_at
    assert result.draft.total == sample.total
    if sample.tax_items is not None:
        assert sum(1 for item in result.draft.items if item.category == TAX_CATEGORY) == sample.tax_items
