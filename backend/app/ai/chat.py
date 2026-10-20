"""Spending chatbot: Claude answers questions about the user's spending (scanned receipts and bank
and card charges) using read-only tools.

The server is stateless: the client sends the visible conversation (text only) with every
request. Within one answer, the tool loop below only appends to the history, so Claude's
thinking blocks stay valid; earlier answers are replayed as plain text, without thinking.
"""

import json
import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import anthropic
from anthropic.types.beta import (
    BetaMessageParam,
    BetaTextBlockParam,
    BetaToolParam,
    BetaToolResultBlockParam,
    BetaToolUseBlock,
)
from pydantic import BaseModel, ValidationError

from app.services.spending_queries import (
    MAX_RECEIPTS,
    MAX_ROWS,
    BreakdownInput,
    FindReceiptsInput,
    MonthOverviewInput,
    SpendingQueries,
)

logger = logging.getLogger(__name__)

MAX_TOKENS = 16_000
MAX_TOOL_ROUNDS = 6
MAX_JSON_RETRIES = 2
# Refusal fallbacks: on a safety decline the API re-runs the request on Anthropic's recommended model.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

SYSTEM_PROMPT = """\
You are the assistant inside LoonieToonie, a household expense app. People scan their \
receipts and link their bank and card accounts, and you help them understand their actual \
spending: where the money goes, how it changes over time, and what is unusual. Be friendly, \
warm, and brief.

Ground every number in the tools. Use them to look things up instead of guessing, and \
report amounts exactly as the tools return them, formatted with the currency (e.g. \
CA$12.49, ¥1,051). The tools do the arithmetic; avoid adding up long lists yourself. If \
the data can't answer a question, say so plainly.

How the data works:
- Each receipt has a date, store, total, currency, and items (name, quantity, unit price, \
category, and an optional subcategory within the category, e.g. Groceries › Bakery). The \
user's categories are listed below as "Category: subcategory; subcategory" (a name may \
contain commas). Items without a subcategory have "" as their subcategory. Taxes and fees \
added on top of prices are items in the "Tax & Fees" category, and discounts are items with \
negative prices.
- Spending also includes card and bank charges imported from linked accounts that no receipt \
explains. Each one looks like a receipt with a single item: the merchant name, the amount, and \
a category (find_purchases marks them "source": "bank"). A charge matched to a scanned receipt \
is not listed separately, so nothing counts twice. Transfers, card payments, and income are not \
spending and never appear.
- So in tool results, a "receipt" is any purchase: a scanned receipt or a bank or card charge. \
When you talk to the person, call them purchases (or transactions), and if you say where the \
numbers come from, say their receipts and bank and card transactions, not only receipts. Say \
"receipt" only for a purchase with "source": "receipt".
- "(not itemized)" is the part of a receipt total that its items don't cover.
- Amounts in different currencies are never added together. Use the default currency \
unless the person asks about another one.
- "Usual month" in month_overview is the average of the previous three months.
- For the month in progress, data only goes up to today, and month_overview compares it \
with the SAME DAYS (day 1 to today) of last month and of the usual month, not with whole \
months. Say so, e.g. "CA$300 so far, compared with CA$250 by this point in a usual month".

Answer in the language the person writes in. Keep answers short: a sentence or two, or a \
few "- " bullet points. No tables or headings. If someone asks how to save money, you can \
point out spending patterns that stand out, but don't lecture: the app is about seeing \
where the money goes, not about budgets.
"""

TOOLS: list[BetaToolParam] = [
    {
        "name": "spending_breakdown",
        "description": (
            "Total spending in a date range, grouped by category, subcategory, store, month, item, or "
            "weekday, "
            "with optional filters. Returns the overall total and one row per group (largest first; "
            f"months and weekdays in order). At most {MAX_ROWS} rows."
        ),
        "eager_input_streaming": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string", "format": "date", "description": "First day, YYYY-MM-DD"},
                "date_to": {
                    "type": "string",
                    "format": "date",
                    "description": "Last day, YYYY-MM-DD (inclusive)",
                },
                "group_by": {
                    "type": "string",
                    "enum": ["category", "subcategory", "store", "month", "item", "weekday"],
                    "description": "subcategory rows are 'Category › Subcategory'",
                },
                "currency": {"type": ["string", "null"], "description": "ISO code; null = default currency"},
                "category": {"type": ["string", "null"], "description": "Only this category (exact name)"},
                "subcategory": {
                    "type": ["string", "null"],
                    "description": "Only this subcategory (exact name; combine with category)",
                },
                "store": {"type": ["string", "null"], "description": "Only stores whose name contains this"},
                "item_contains": {
                    "type": ["string", "null"],
                    "description": "Only items whose name contains this",
                },
                "limit": {"type": ["integer", "null"], "description": f"Max rows (1-{MAX_ROWS})"},
            },
            "required": ["date_from", "date_to", "group_by"],
            "additionalProperties": False,
        },
    },
    {
        "name": "find_purchases",
        "description": (
            "Individual purchases in a date range (newest first): scanned receipts with their items and "
            "bank or card charges, optionally filtered by category, subcategory, store, or item name. "
            f"Returns the count and total of all matches and up to {MAX_RECEIPTS} purchases."
        ),
        "eager_input_streaming": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string", "format": "date"},
                "date_to": {"type": "string", "format": "date"},
                "currency": {"type": ["string", "null"]},
                "category": {"type": ["string", "null"]},
                "subcategory": {"type": ["string", "null"]},
                "store": {"type": ["string", "null"]},
                "item_contains": {"type": ["string", "null"]},
                "limit": {"type": ["integer", "null"], "description": f"Max purchases (1-{MAX_RECEIPTS})"},
            },
            "required": ["date_from", "date_to"],
            "additionalProperties": False,
        },
    },
    {
        "name": "month_overview",
        "description": (
            "The dashboard numbers for one month: total, comparison with last month and the usual "
            "month, spending per category with usual amounts, top stores and items, and notable changes."
        ),
        "eager_input_streaming": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "month": {"type": "string", "description": "YYYY-MM"},
                "currency": {"type": ["string", "null"]},
            },
            "required": ["month"],
            "additionalProperties": False,
        },
    },
]

TOOL_STATUS = {
    "spending_breakdown": "Adding up your spending…",
    "find_purchases": "Looking through your purchases…",
    "month_overview": "Checking the month…",
}


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True)
class ChatEvent:
    type: Literal["text", "status", "reset", "error", "done", "conversation"]
    text: str = ""

    def to_json(self) -> str:
        return json.dumps({"type": self.type, "text": self.text}, ensure_ascii=False)


class ChatRunner(Protocol):
    def run(
        self, history: list[ChatMessage], queries: SpendingQueries, categories: list[str]
    ) -> Iterator[ChatEvent]: ...


def run_tool(queries: SpendingQueries, name: str, raw_input: object) -> tuple[str, bool]:
    """Execute one tool call. Returns (JSON result, is_error). Inputs are validated first."""
    handlers: dict[str, tuple[type[BaseModel], Callable[[Any], dict[str, Any]]]] = {
        "spending_breakdown": (BreakdownInput, queries.spending_breakdown),
        "find_purchases": (FindReceiptsInput, queries.find_receipts),
        "month_overview": (MonthOverviewInput, queries.month_overview),
    }
    if name not in handlers:
        return json.dumps({"error": f"Unknown tool {name}"}), True
    model, handler = handlers[name]
    try:
        parsed = model.model_validate(raw_input)
    except ValidationError as exc:
        problems = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
        return json.dumps({"error": "Invalid input", "problems": problems}), True
    return json.dumps(handler(parsed), ensure_ascii=False), False


class ClaudeChatRunner:
    def __init__(self, client: anthropic.Anthropic, model: str) -> None:
        self._client = client
        self._model = model

    def run(
        self, history: list[ChatMessage], queries: SpendingQueries, categories: list[str]
    ) -> Iterator[ChatEvent]:
        context = {**queries.context(), "categories": categories}
        system: list[BetaTextBlockParam] = [
            # Stable instructions first and cached; per-user facts after the breakpoint.
            {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}},
            {
                "type": "text",
                "text": "Facts about this person's data:\n" + json.dumps(context, ensure_ascii=False),
            },
        ]
        messages: list[BetaMessageParam] = [{"role": m.role, "content": m.content} for m in history]

        rounds = 0
        json_retries = 0
        while True:
            try:
                with self._client.beta.messages.stream(
                    model=self._model,
                    max_tokens=MAX_TOKENS,
                    system=system,
                    tools=TOOLS,
                    messages=messages,
                    output_config={"effort": "medium"},
                    betas=[FALLBACK_BETA],
                    fallbacks="default",
                ) as stream:
                    for event in stream:
                        if event.type == "text":
                            yield ChatEvent("text", event.text)
                        elif event.type == "content_block_start":
                            block = event.content_block
                            if isinstance(block, BetaToolUseBlock):
                                yield ChatEvent("status", TOOL_STATUS.get(block.name, "Working…"))
                            elif block.type == "fallback":
                                # A declined partial answer is replaced by the fallback model's answer.
                                yield ChatEvent("reset")
                    response = stream.get_final_message()
                json_retries = 0
            except ValueError:
                # Eager tool-input streaming: JSON the SDK couldn't parse. Re-issue the turn (bounded).
                json_retries += 1
                if json_retries > MAX_JSON_RETRIES:
                    yield ChatEvent("error", "Sorry, something went wrong. Please ask again.")
                    return
                yield ChatEvent("reset")
                continue
            except anthropic.RateLimitError:
                yield ChatEvent("error", "The assistant is busy right now. Please try again in a minute.")
                return
            except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
                logger.warning("Chat request failed: %s", exc)
                yield ChatEvent("error", "The assistant isn't available right now. Please try again.")
                return

            if response.stop_reason == "refusal":
                yield ChatEvent("reset")
                yield ChatEvent("error", "Sorry, I can't help with that one. Try asking about your spending.")
                return

            tool_uses = [block for block in response.content if isinstance(block, BetaToolUseBlock)]
            if not tool_uses:
                yield ChatEvent("done")
                return
            if response.stop_reason == "max_tokens":
                yield ChatEvent("error", "That answer got too long. Please ask a narrower question.")
                return
            rounds += 1
            if rounds > MAX_TOOL_ROUNDS:
                yield ChatEvent(
                    "error", "That question needed too many lookups. Please ask a narrower question."
                )
                return

            results: list[BetaToolResultBlockParam] = []
            for block in tool_uses:
                content, is_error = run_tool(queries, block.name, block.input)
                results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": content, "is_error": is_error}
                )
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": results})
