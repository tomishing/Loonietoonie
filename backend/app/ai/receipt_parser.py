"""Turn OCR text (and, on retry, the photo) into a draft receipt with Claude.

Claude's output is constrained to a JSON schema (structured outputs), so it always
parses. Whether the numbers add up is checked afterwards in app/services/scan.py.
"""

import base64
import logging
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, Protocol

import anthropic
from anthropic.types import ContentBlockParam, ImageBlockParam, OutputConfigParam
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

MAX_TOKENS = 16_000
REQUEST_TIMEOUT_SECONDS = 90.0

SYSTEM_PROMPT = """\
You read shopping receipts and return their contents as JSON. Receipts may be in any \
language (often English, French, or Japanese). The text comes from OCR, so it can contain \
misread characters (O/0, l/1, S/5) and broken line order; fix obvious misreads when the \
arithmetic confirms them. Only report what the receipt shows. Never invent items.

Money values are plain decimal strings with "." as the decimal separator and no currency \
symbols or thousands separators: "4.99", "1080", "-1.00".

store: the business name as printed, in its original language and script.

purchased_at: the purchase date as YYYY-MM-DD, or null if no date is shown. Japanese era \
years: 令和N年 is year 2018+N. If day and month are ambiguous (03/04/26), choose the reading \
that is not in the future and is closest to today.

currency: the ISO 4217 code. "$" alone means the default currency given below, unless the \
receipt clearly shows another dollar (US$, USD, a US address). ¥ or 円 is JPY, € is EUR. \
Use null if there is no clue at all.

items: one entry per purchased line.
- quantity is the number of units ("1" if not shown); unit_price is the price of ONE unit. \
"2 @ 1.99 3.98" means quantity "2", unit_price "1.99".
- Weighed items ("0.512 kg @ 4.39/kg 2.25"): quantity "1", unit_price is the line amount \
("2.25"), and put the weight in the name ("Bananas 0.512 kg").
- Discounts, coupons, and points redeemed are their own items with a negative unit_price, \
in the category of the product they discount.
- Taxes and fees ADDED ON TOP of the item prices are their own items in the "Tax & Fees" \
category (or "Other" if that category is not offered): GST, HST, PST, QST, TPS, TVQ, US \
sales tax, Japanese 外税 consumption tax, bottle deposits, eco fees, bag fees, tips, \
service charges.
- Tax that is ALREADY INCLUDED in the item prices is informational, so do NOT add it as an \
item: Japanese 内税 lines such as "(内消費税等 ¥80)", European VAT summaries.
- Skip subtotals, payment lines (cash tendered, change, card numbers), loyalty balances, \
and savings summaries that repeat discounts already listed.
- name: the item name as printed, in its original language.
- category: the best match from the allowed list, judged from the item name in any language.

total: the amount paid, including tax (TOTAL, Grand total, TOTAL À PAYER, 合計), not the \
subtotal, the amount tendered, or the change.

Before answering, check that the sum of quantity × unit_price over all items (discounts \
negative, tax items included) equals total. If it doesn't, re-read the receipt for a \
missed or misread line.
"""


class ParsedItem(BaseModel):
    name: str
    quantity: str
    unit_price: str
    category: str


class ParsedReceipt(BaseModel):
    store: str
    purchased_at: str | None
    currency: str | None
    total: str
    items: list[ParsedItem]


ImageMediaType = Literal["image/jpeg", "image/png", "image/webp"]


@dataclass(frozen=True)
class ReceiptImage:
    data: bytes
    media_type: ImageMediaType


class ReceiptParseError(Exception):
    """Claude could not produce a receipt (API error, refusal, or cut-off output)."""


class ReceiptParser(Protocol):
    def parse(
        self,
        ocr_text: str,
        categories: list[str],
        default_currency: str,
        today: date,
        image: ReceiptImage | None = None,
    ) -> ParsedReceipt: ...


def output_schema(categories: list[str]) -> dict[str, Any]:
    """JSON schema for Claude's answer. Categories are an enum of the user's own categories."""
    money = {"type": "string", "description": 'Plain decimal string, e.g. "4.99" or "-1.00"'}
    return {
        "type": "object",
        "properties": {
            "store": {"type": "string"},
            "purchased_at": {"anyOf": [{"type": "string", "format": "date"}, {"type": "null"}]},
            "currency": {
                "anyOf": [{"type": "string", "description": "ISO 4217 code, e.g. CAD"}, {"type": "null"}]
            },
            "total": money,
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "quantity": {"type": "string", "description": 'Units bought, e.g. "1" or "2"'},
                        "unit_price": money,
                        "category": {"type": "string", "enum": categories},
                    },
                    "required": ["name", "quantity", "unit_price", "category"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["store", "purchased_at", "currency", "total", "items"],
        "additionalProperties": False,
    }


class ClaudeReceiptParser:
    def __init__(self, client: anthropic.Anthropic, model: str) -> None:
        self._client = client
        self._model = model

    def parse(
        self,
        ocr_text: str,
        categories: list[str],
        default_currency: str,
        today: date,
        image: ReceiptImage | None = None,
    ) -> ParsedReceipt:
        content: list[ContentBlockParam] = []
        if image is not None:
            image_block: ImageBlockParam = {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": image.media_type,
                    "data": base64.standard_b64encode(image.data).decode("ascii"),
                },
            }
            content.append(image_block)
        content.append(
            {"type": "text", "text": _user_prompt(ocr_text, categories, default_currency, today, image)}
        )

        output_config: OutputConfigParam = {
            "effort": "medium",
            "format": {"type": "json_schema", "schema": output_schema(categories)},
        }
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": content}],
                output_config=output_config,
            )
        except anthropic.RateLimitError as exc:
            raise ReceiptParseError("Claude is busy (rate limited)") from exc
        except anthropic.APIStatusError as exc:
            raise ReceiptParseError(f"Claude API error {exc.status_code}") from exc
        except anthropic.APIConnectionError as exc:
            raise ReceiptParseError("Could not reach Claude") from exc

        if response.stop_reason == "refusal":
            raise ReceiptParseError("Claude declined to read this receipt")
        if response.stop_reason == "max_tokens":
            raise ReceiptParseError("Claude's answer was cut off")

        text = next((block.text for block in response.content if block.type == "text"), None)
        if text is None:
            raise ReceiptParseError("Claude returned no answer")
        try:
            return ParsedReceipt.model_validate_json(text)
        except ValidationError as exc:
            logger.warning("Unexpected receipt JSON (request %s)", response._request_id)
            raise ReceiptParseError("Claude returned an unexpected answer") from exc


def _user_prompt(
    ocr_text: str, categories: list[str], default_currency: str, today: date, image: ReceiptImage | None
) -> str:
    source = (
        "A photo of the receipt is attached; the OCR text below may be incomplete or wrong, "
        "so trust the photo where they disagree."
        if image is not None
        else "Here is the OCR text of the receipt."
    )
    return (
        f"Today is {today.isoformat()}. The default currency is {default_currency}.\n"
        f"Allowed categories: {', '.join(categories)}.\n\n"
        f"{source}\n\n<ocr_text>\n{ocr_text.strip() or '(no text recognized)'}\n</ocr_text>"
    )


_clients: dict[str, anthropic.Anthropic] = {}


def claude_client(api_key: str) -> anthropic.Anthropic:
    """One shared client per API key (it keeps a connection pool)."""
    client = _clients.get(api_key)
    if client is None:
        client = anthropic.Anthropic(api_key=api_key, timeout=REQUEST_TIMEOUT_SECONDS, max_retries=2)
        _clients[api_key] = client
    return client
