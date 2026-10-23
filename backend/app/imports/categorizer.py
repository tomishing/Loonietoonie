"""Suggest categories for merchants from imported statement files, with Claude.

Only the merchant text, the account type, and whether the money went out or came in are sent:
no amounts, dates, or account numbers. The answer is constrained to a JSON schema and checked
against the user's categories afterwards (services/imports.py).
"""

import logging
from typing import Any, Literal, Protocol

import anthropic
from anthropic.types import OutputConfigParam
from pydantic import BaseModel, ValidationError

from app.ai.receipt_parser import category_lines
from app.storage.models import BankTransactionKind, Category

logger = logging.getLogger(__name__)

MAX_TOKENS = 16_000
# Merchants per request.
BATCH_SIZE = 200

SYSTEM_PROMPT = """\
You categorize bank and credit card transactions for a household spending app. Each entry is \
the text a bank shows for a transaction (often abbreviated, in English or French, with store \
numbers and city names), the account type, and whether money went out of the account or came in.

For each entry, answer:
- kind: "spending" for purchases and their refunds; "transfer" for moving money between the \
household's own accounts, paying off a credit card, or sending money to a person; "income" for \
pay, government benefits, interest, and other money earned.
- category: for spending, the best match from the allowed list, judged from what the business \
sells; "Other" when the text doesn't say what the business is. For transfer and income, "".
- subcategory: the best match among the subcategories listed for THAT category, or null if none \
clearly fits.

Money coming into a credit card is a refund of a purchase (spending, in the purchase's category) \
unless the text says it is a payment.
"""


class MerchantToCategorize(BaseModel):
    id: int
    text: str
    account_type: Literal["bank account", "credit card"]
    money: Literal["out", "in"]


class MerchantCategory(BaseModel):
    id: int
    kind: BankTransactionKind
    category: str
    subcategory: str | None = None


class _Answer(BaseModel):
    merchants: list[MerchantCategory]


class CategorizeError(Exception):
    """Claude could not suggest categories (API error, refusal, or cut-off output)."""


class MerchantCategorizer(Protocol):
    def categorize(
        self, merchants: list[MerchantToCategorize], categories: list[Category]
    ) -> list[MerchantCategory]: ...


def output_schema(categories: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "merchants": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "kind": {"type": "string", "enum": ["spending", "transfer", "income"]},
                        "category": {"type": "string", "enum": [*categories, ""]},
                        "subcategory": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                    },
                    "required": ["id", "kind", "category", "subcategory"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["merchants"],
        "additionalProperties": False,
    }


class ClaudeMerchantCategorizer:
    def __init__(self, client: anthropic.Anthropic, model: str) -> None:
        self._client = client
        self._model = model

    def categorize(
        self, merchants: list[MerchantToCategorize], categories: list[Category]
    ) -> list[MerchantCategory]:
        found: list[MerchantCategory] = []
        for start in range(0, len(merchants), BATCH_SIZE):
            found.extend(self._batch(merchants[start : start + BATCH_SIZE], categories))
        return found

    def _batch(
        self, merchants: list[MerchantToCategorize], categories: list[Category]
    ) -> list[MerchantCategory]:
        allowed = "\n".join(category_lines(category) for category in categories)
        entries = "\n".join(m.model_dump_json() for m in merchants)
        prompt = (
            f"Allowed categories, each with its subcategories and what they cover:\n{allowed}\n\n"
            f"Transactions, one JSON object per line:\n<transactions>\n{entries}\n</transactions>"
        )
        output_config: OutputConfigParam = {
            "effort": "low",
            "format": {"type": "json_schema", "schema": output_schema([c.name for c in categories])},
        }
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
                output_config=output_config,
            )
        except anthropic.APIError as exc:
            raise CategorizeError("Claude could not be reached") from exc
        if response.stop_reason in ("refusal", "max_tokens"):
            raise CategorizeError(f"Claude stopped ({response.stop_reason})")
        text = next((block.text for block in response.content if block.type == "text"), None)
        if text is None:
            raise CategorizeError("Claude returned no answer")
        try:
            return _Answer.model_validate_json(text).merchants
        except ValidationError as exc:
            logger.warning("Unexpected categorization JSON (request %s)", response._request_id)
            raise CategorizeError("Claude returned an unexpected answer") from exc
