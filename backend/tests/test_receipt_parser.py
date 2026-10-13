import json
from datetime import date
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest

from app.ai.receipt_parser import (
    SYSTEM_PROMPT,
    ClaudeReceiptParser,
    ReceiptImage,
    ReceiptParseError,
    output_schema,
)

CATEGORIES = ["Groceries", "Tax & Fees", "Other"]
ANSWER = {
    "store": "FreshCo",
    "purchased_at": "2026-10-05",
    "currency": "CAD",
    "total": "5.65",
    "items": [
        {"name": "Milk", "quantity": "1", "unit_price": "5.00", "category": "Groceries"},
        {"name": "HST", "quantity": "1", "unit_price": "0.65", "category": "Tax & Fees"},
    ],
}


class FakeMessages:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.requests: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def message(text: str | None = None, stop_reason: str = "end_turn") -> Any:
    content = [SimpleNamespace(type="thinking", thinking="")]
    if text is not None:
        content.append(SimpleNamespace(type="text", text=text))
    return SimpleNamespace(content=content, stop_reason=stop_reason, _request_id="req_test")


def make_parser(messages: FakeMessages) -> ClaudeReceiptParser:
    client = SimpleNamespace(messages=messages)
    return ClaudeReceiptParser(client, "claude-haiku-5-5")  # type: ignore[arg-type]


def parse(parser: ClaudeReceiptParser, image: ReceiptImage | None = None) -> Any:
    return parser.parse(
        "FRESHCO\nMILK 5.00\nHST 0.65\nTOTAL 5.65", CATEGORIES, "CAD", date(2026, 10, 13), image
    )


def test_sends_text_only_request_with_strict_schema() -> None:
    messages = FakeMessages(message(json.dumps(ANSWER)))

    result = parse(make_parser(messages))

    assert result.total == "5.65"
    assert result.items[1].category == "Tax & Fees"
    [request] = messages.requests
    assert request["model"] == "claude-haiku-5-5"
    assert request["system"] == SYSTEM_PROMPT
    assert request["output_config"]["format"] == {"type": "json_schema", "schema": output_schema(CATEGORIES)}
    [content] = request["messages"][0]["content"]
    assert content["type"] == "text"
    assert "MILK 5.00" in content["text"]
    assert "default currency is CAD" in content["text"]
    assert "Tax & Fees" in content["text"]


def test_retry_attaches_photo_before_text() -> None:
    messages = FakeMessages(message(json.dumps(ANSWER)))

    parse(make_parser(messages), ReceiptImage(data=b"\xff\xd8jpeg", media_type="image/jpeg"))

    image_block, text_block = messages.requests[0]["messages"][0]["content"]
    assert image_block["type"] == "image"
    assert image_block["source"] == {"type": "base64", "media_type": "image/jpeg", "data": "/9hqcGVn"}
    assert "photo of the receipt is attached" in text_block["text"]


def test_schema_limits_categories_and_forbids_extra_fields() -> None:
    schema = output_schema(CATEGORIES)

    item_schema = schema["properties"]["items"]["items"]
    assert item_schema["properties"]["category"]["enum"] == CATEGORIES
    assert schema["additionalProperties"] is False
    assert item_schema["additionalProperties"] is False


@pytest.mark.parametrize(
    "response",
    [
        message(json.dumps(ANSWER), stop_reason="refusal"),
        message('{"store": "Fr', stop_reason="max_tokens"),
        message(None),
    ],
)
def test_unusable_answers_raise(response: Any) -> None:
    with pytest.raises(ReceiptParseError):
        parse(make_parser(FakeMessages(response)))


def test_unexpected_json_raises() -> None:
    with pytest.raises(ReceiptParseError):
        parse(make_parser(FakeMessages(message('{"store": "x"}'))))


def _api_error(status: int) -> anthropic.APIStatusError:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx2.Response(
        status, request=request, json={"type": "error", "error": {"type": "x", "message": "x"}}
    )
    error_class = anthropic.RateLimitError if status == 429 else anthropic.InternalServerError
    return error_class("error", response=response, body=None)


@pytest.mark.parametrize(
    "error",
    [
        _api_error(429),
        _api_error(529),
        anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")),
    ],
)
def test_api_errors_raise_parse_error(error: Exception) -> None:
    with pytest.raises(ReceiptParseError):
        parse(make_parser(FakeMessages(error=error)))
