"""Read-only questions about the user's spending, answered with pandas. Used as the chatbot's tools.

Every number the chatbot reports comes from here, never from the model's own arithmetic.
Inputs are Pydantic models because they arrive from the model and must be validated.
"""

from datetime import date
from decimal import Decimal
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, Field, model_validator

from app.services.summary import build_frames, summarize
from app.storage.models import BANK_RECEIPT_PREFIX, Receipt, ReceiptFilter

MAX_ROWS = 50
MAX_RECEIPTS = 30
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

GroupBy = Literal["category", "subcategory", "store", "month", "item", "weekday"]
SUBCATEGORY_SEPARATOR = " › "


class _Range(BaseModel):
    date_from: date
    date_to: date
    currency: str | None = None

    @model_validator(mode="after")
    def _ordered(self) -> "_Range":
        if self.date_from > self.date_to:
            raise ValueError("date_from must be on or before date_to")
        return self


class BreakdownInput(_Range):
    group_by: GroupBy
    category: str | None = None
    subcategory: str | None = None
    store: str | None = None
    item_contains: str | None = None
    limit: int | None = Field(default=None, ge=1, le=MAX_ROWS)


class FindReceiptsInput(_Range):
    category: str | None = None
    subcategory: str | None = None
    store: str | None = None
    item_contains: str | None = None
    limit: int | None = Field(default=None, ge=1, le=MAX_RECEIPTS)


class MonthOverviewInput(BaseModel):
    month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    currency: str | None = None


def _money(value: Any) -> str:
    amount = value if isinstance(value, Decimal) else Decimal(str(value))
    return format(amount, "f")


def _contains(series: "pd.Series[Any]", text: str) -> "pd.Series[bool]":
    return series.astype(str).str.casefold().str.contains(text.strip().casefold(), regex=False)


class SpendingQueries:
    def __init__(self, receipts: list[Receipt], default_currency: str, today: date) -> None:
        self._receipts = receipts
        self._default_currency = default_currency
        self._today = today

    def _currency(self, requested: str | None) -> str:
        return (requested or self._default_currency).strip().upper()

    def _lines(self, query: _Range) -> tuple[str, pd.DataFrame]:
        currency = self._currency(query.currency)
        lines = build_frames([r for r in self._receipts if r.currency == currency]).lines
        selected = lines[
            (lines["date"] >= query.date_from.isoformat()) & (lines["date"] <= query.date_to.isoformat())
        ]
        return currency, selected

    def spending_breakdown(self, query: BreakdownInput) -> dict[str, Any]:
        currency, lines = self._lines(query)
        if query.category:
            lines = lines[lines["category"].str.casefold() == query.category.strip().casefold()]
        if query.subcategory:
            lines = lines[lines["subcategory"].str.casefold() == query.subcategory.strip().casefold()]
        if query.store:
            lines = lines[_contains(lines["store"], query.store)]
        if query.item_contains:
            lines = lines[_contains(lines["name"], query.item_contains)]

        if lines.empty:
            return {"currency": currency, "total": "0", "receipts": 0, "rows": []}

        keys: pd.Series[Any]
        if query.group_by == "category":
            keys = lines["category"]
        elif query.group_by == "subcategory":
            # Same subcategory names can exist under different categories, so keep both.
            keys = lines["category"] + lines["subcategory"].map(
                lambda sub: SUBCATEGORY_SEPARATOR + sub if sub else SUBCATEGORY_SEPARATOR + "(none)"
            )
        elif query.group_by == "store":
            keys = lines["store"]
        elif query.group_by == "month":
            keys = lines["month"]
        elif query.group_by == "item":
            keys = lines["key"]
        else:
            keys = pd.to_datetime(lines["date"]).dt.dayofweek.map(lambda d: WEEKDAYS[int(d)])

        grouped = lines.assign(group=keys).groupby("group")
        rows = []
        for key, group in grouped:
            label = str(group["name"].iloc[0]) if query.group_by == "item" else str(key)
            if query.group_by == "item" and not label:
                continue  # amounts not covered by items
            row: dict[str, Any] = {"key": label, "total": _money(group["amount"].sum())}
            if query.group_by == "item":
                row["times_bought"] = int(len(group))
            else:
                row["receipts"] = int(group["receipt_id"].nunique())
            rows.append(row)

        if query.group_by == "month":
            rows.sort(key=lambda r: r["key"])
        elif query.group_by == "weekday":
            rows.sort(key=lambda r: WEEKDAYS.index(r["key"]))
        else:
            rows.sort(key=lambda r: -Decimal(r["total"]))
        limit = query.limit or MAX_ROWS
        return {
            "currency": currency,
            "total": _money(lines["amount"].sum()),
            "receipts": int(lines["receipt_id"].nunique()),
            "rows": rows[:limit],
            "rows_omitted": max(0, len(rows) - limit),
        }

    def find_receipts(self, query: FindReceiptsInput) -> dict[str, Any]:
        currency = self._currency(query.currency)
        matches: list[Receipt] = []
        for receipt in self._receipts:
            if receipt.currency != currency or not (query.date_from <= receipt.purchased_at <= query.date_to):
                continue
            if query.store and query.store.strip().casefold() not in receipt.store.casefold():
                continue
            item_filter = ReceiptFilter(category=query.category, subcategory=query.subcategory)
            if item_filter.filters_items and not any(item_filter.matches_item(i) for i in receipt.items):
                continue
            if query.item_contains and not any(
                query.item_contains.strip().casefold() in i.name.casefold() for i in receipt.items
            ):
                continue
            matches.append(receipt)
        matches.sort(key=lambda r: (r.purchased_at, r.created_at), reverse=True)
        limit = query.limit or MAX_RECEIPTS
        return {
            "currency": currency,
            "matching_receipts": len(matches),
            "total_of_matching_receipts": _money(sum((r.total for r in matches), Decimal(0))),
            "receipts": [
                {
                    "date": r.purchased_at.isoformat(),
                    "source": "bank" if r.receipt_id.startswith(BANK_RECEIPT_PREFIX) else "receipt",
                    "store": r.store,
                    "total": _money(r.total),
                    "items": [
                        {
                            "name": i.name,
                            "quantity": _money(i.quantity),
                            "unit_price": _money(i.price),
                            "category": i.category,
                            "subcategory": i.subcategory,
                        }
                        for i in r.items
                    ],
                }
                for r in matches[:limit]
            ],
            "receipts_omitted": max(0, len(matches) - limit),
        }

    def month_overview(self, query: MonthOverviewInput) -> dict[str, Any]:
        summary = summarize(
            self._receipts,
            month=query.month,
            currency=self._currency(query.currency),
            default_currency=self._default_currency,
            today=self._today,
        )
        data: dict[str, Any] = summary.model_dump(
            mode="json", exclude={"trend", "trend_categories", "currencies"}
        )
        return data

    def context(self) -> dict[str, Any]:
        """Facts the model needs before it can ask good questions of the tools."""
        dates = [r.purchased_at for r in self._receipts]
        counts: dict[str, int] = {}
        for receipt in self._receipts:
            counts[receipt.currency] = counts.get(receipt.currency, 0) + 1
        return {
            "today": self._today.isoformat(),
            "default_currency": self._default_currency,
            "receipts_by_currency": counts,
            "first_receipt": min(dates).isoformat() if dates else None,
            "last_receipt": max(dates).isoformat() if dates else None,
        }
