"""Spending patterns for the dashboard, computed with pandas from the receipts and items.

All amounts stay Decimal (object columns), and one currency is summarized at a time.
For the current month, comparisons use the same days of earlier months, so a month
in progress isn't compared with full months.
"""

import calendar
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import pandas as pd
from pydantic import BaseModel

from app.sheets.schema import TAX_CATEGORY
from app.storage.models import Receipt

COMPARE_MONTHS = 3
TREND_MONTHS = 12
TREND_TOP_CATEGORIES = 5
TOP_LIST_SIZE = 5
MAX_CHANGES = 5

# Smaller categories folded together in the trend (never equal to a real category name).
OTHER = "(other categories)"
# Receipts whose total differs from their items (e.g. tax entered only in the total).
NOT_ITEMIZED = "(not itemized)"

# A category change is notable when it moves at least this share against its average
# AND by at least this share of the average month's total spending.
CHANGE_RATIO = Decimal("0.25")
CHANGE_SHARE_OF_SPENDING = Decimal("0.05")
NEW_CATEGORY_SHARE = Decimal("0.05")
MAX_NEW_STORES = 3


class Period(BaseModel):
    month: str
    start: date
    end: date
    # True for the month in progress: comparisons then cover days 1..end_day of earlier months.
    partial: bool


class SubcategorySpending(BaseModel):
    subcategory: str  # "" for items without a subcategory
    total: Decimal
    average_total: Decimal | None


class CategorySpending(BaseModel):
    category: str
    total: Decimal
    previous_total: Decimal
    average_total: Decimal | None
    share: Decimal  # of this period's total, 0–1
    # Drill-down; empty when none of the category's items have a subcategory.
    subcategories: list[SubcategorySpending] = []


class TrendMonth(BaseModel):
    month: str
    total: Decimal
    by_category: dict[str, Decimal]


class StoreSpending(BaseModel):
    store: str
    total: Decimal
    visits: int


class ItemFrequency(BaseModel):
    name: str
    times: int
    total: Decimal


class Change(BaseModel):
    kind: str  # category_up | category_down | category_new | new_store
    category: str | None = None
    store: str | None = None
    amount: Decimal
    baseline: Decimal | None = None


class Summary(BaseModel):
    currency: str
    currencies: list[str]
    period: Period
    total: Decimal
    receipt_count: int
    previous_total: Decimal
    average_total: Decimal | None
    categories: list[CategorySpending]
    trend: list[TrendMonth]
    trend_categories: list[str]
    top_stores: list[StoreSpending]
    top_items: list[ItemFrequency]
    changes: list[Change]


def shift_month(month: str, delta: int) -> str:
    year, number = (int(part) for part in month.split("-"))
    index = year * 12 + (number - 1) + delta
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def _cents(value: Decimal) -> Decimal:
    """Averages are rounded to 0.01 so the API never returns 28-digit fractions."""
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _dec(value: object) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _sum(series: "pd.Series[Any]") -> Decimal:
    return _dec(series.sum()) if len(series) else Decimal(0)


@dataclass(frozen=True)
class Frames:
    receipts: pd.DataFrame  # receipt_id, date, month, day, store, total
    lines: pd.DataFrame  # receipt_id, date, month, day, store, category, subcategory, name, key, amount


def build_frames(receipts: list[Receipt]) -> Frames:
    """Receipts and item lines as DataFrames (amounts stay Decimal). Shared with the chat tools."""
    receipt_rows: list[dict[str, object]] = []
    line_rows: list[dict[str, object]] = []
    for receipt in receipts:
        iso_date = receipt.purchased_at.isoformat()
        month = iso_date[:7]
        day = receipt.purchased_at.day
        receipt_rows.append(
            {
                "receipt_id": receipt.receipt_id,
                "date": iso_date,
                "month": month,
                "day": day,
                "store": receipt.store.strip(),
                "total": receipt.total,
            }
        )
        itemized = Decimal(0)
        for item in receipt.items:
            amount = item.quantity * item.price
            itemized += amount
            line_rows.append(
                {
                    "receipt_id": receipt.receipt_id,
                    "date": iso_date,
                    "month": month,
                    "day": day,
                    "store": receipt.store.strip(),
                    "category": item.category,
                    "subcategory": item.subcategory,
                    "name": item.name.strip(),
                    "key": item.name.strip().casefold(),
                    "amount": amount,
                }
            )
        if receipt.total != itemized:
            line_rows.append(
                {
                    "receipt_id": receipt.receipt_id,
                    "date": iso_date,
                    "month": month,
                    "day": day,
                    "store": receipt.store.strip(),
                    "category": NOT_ITEMIZED,
                    "subcategory": "",
                    "name": "",
                    "key": "",
                    "amount": receipt.total - itemized,
                }
            )
    receipt_columns = ["receipt_id", "date", "month", "day", "store", "total"]
    line_columns = [
        "receipt_id",
        "date",
        "month",
        "day",
        "store",
        "category",
        "subcategory",
        "name",
        "key",
        "amount",
    ]
    return Frames(
        receipts=pd.DataFrame(receipt_rows, columns=receipt_columns).astype({"total": object}),
        lines=pd.DataFrame(line_rows, columns=line_columns).astype({"amount": object}),
    )


def _window(frame: pd.DataFrame, month: str, last_day: int | None) -> pd.DataFrame:
    selected = frame[frame["month"] == month]
    return selected[selected["day"] <= last_day] if last_day is not None else selected


def _by_category(lines: pd.DataFrame) -> dict[str, Decimal]:
    if lines.empty:
        return {}
    return {str(name): _dec(value) for name, value in lines.groupby("category")["amount"].sum().items()}


def _by_subcategory(lines: pd.DataFrame) -> dict[tuple[str, str], Decimal]:
    if lines.empty:
        return {}
    grouped = lines.groupby(["category", "subcategory"])["amount"].sum()
    result: dict[tuple[str, str], Decimal] = {}
    for key, value in grouped.items():
        pair: tuple[object, ...] = tuple(key) if isinstance(key, tuple) else (key, "")
        result[(str(pair[0]), str(pair[1]))] = _dec(value)
    return result


def _subcategories(
    category: str,
    current: dict[tuple[str, str], Decimal],
    history: list[dict[tuple[str, str], Decimal]],
) -> list[SubcategorySpending]:
    """This period's spending per subcategory of one category, with the usual month."""
    names = {sub for cat, sub in current if cat == category}
    for month_history in history:
        names |= {sub for cat, sub in month_history if cat == category}
    if not any(names):
        return []
    rows = [
        SubcategorySpending(
            subcategory=name,
            total=current.get((category, name), Decimal(0)),
            average_total=_cents(
                sum((h.get((category, name), Decimal(0)) for h in history), Decimal(0)) / len(history)
            )
            if history
            else None,
        )
        for name in names
    ]
    # Largest first; "no subcategory" last so the named ones lead.
    rows.sort(key=lambda r: (r.subcategory == "", -r.total, -(r.average_total or 0), r.subcategory))
    return rows


def summarize(
    receipts: list[Receipt], month: str, currency: str, default_currency: str, today: date
) -> Summary:
    currencies = sorted({r.currency for r in receipts}, key=lambda c: (c != default_currency, c))
    if default_currency not in currencies:
        currencies.insert(0, default_currency)

    frames = build_frames([r for r in receipts if r.currency == currency])
    receipts_df, lines = frames.receipts, frames.lines

    year, number = (int(part) for part in month.split("-"))
    month_days = calendar.monthrange(year, number)[1]
    partial = (year, number) == (today.year, today.month)
    last_day = today.day if partial else None
    period = Period(
        month=month,
        start=date(year, number, 1),
        end=date(year, number, last_day or month_days),
        partial=partial,
    )

    # --- totals: this period, the previous month, and the average of earlier months -------
    first_month = str(receipts_df["month"].min()) if not receipts_df.empty else None
    earlier = [shift_month(month, -offset) for offset in range(1, COMPARE_MONTHS + 1)]
    # Only months since the first receipt count towards the average (no invented zeros).
    history = [m for m in earlier if first_month is not None and m >= first_month]

    current = _window(receipts_df, month, last_day)
    total = _sum(current["total"])
    previous_total = _sum(_window(receipts_df, earlier[0], last_day)["total"])
    history_totals = [_sum(_window(receipts_df, m, last_day)["total"]) for m in history]
    average_total = _cents(sum(history_totals, Decimal(0)) / len(history)) if history else None

    # --- categories ------------------------------------------------------------------
    current_by_category = _by_category(_window(lines, month, last_day))
    current_by_sub = _by_subcategory(_window(lines, month, last_day))
    history_by_sub = [_by_subcategory(_window(lines, m, last_day)) for m in history]
    previous_by_category = _by_category(_window(lines, earlier[0], last_day))
    history_by_category = [_by_category(_window(lines, m, last_day)) for m in history]
    names = set(current_by_category) | set(previous_by_category)
    for month_history in history_by_category:
        names |= set(month_history)

    categories: list[CategorySpending] = []
    for name in names:
        spent = current_by_category.get(name, Decimal(0))
        average = (
            _cents(sum((h.get(name, Decimal(0)) for h in history_by_category), Decimal(0)) / len(history))
            if history
            else None
        )
        categories.append(
            CategorySpending(
                category=name,
                total=spent,
                previous_total=previous_by_category.get(name, Decimal(0)),
                average_total=average,
                share=(spent / total).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
                if total
                else Decimal(0),
                subcategories=_subcategories(name, current_by_sub, history_by_sub),
            )
        )
    categories.sort(key=lambda c: (-c.total, -(c.average_total or 0), c.category))

    # --- 12-month trend (full months; the current month as far as it goes) -----------------
    trend_months = [shift_month(month, -offset) for offset in range(TREND_MONTHS - 1, -1, -1)]
    trend_lines = lines[lines["month"].isin(trend_months)]
    ranking = _by_category(trend_lines)
    top = [
        name for name, _ in sorted(ranking.items(), key=lambda kv: (-kv[1], kv[0])) if name != NOT_ITEMIZED
    ]
    trend_categories = top[:TREND_TOP_CATEGORIES]
    trend: list[TrendMonth] = []
    for trend_month in trend_months:
        month_spent = _by_category(trend_lines[trend_lines["month"] == trend_month])
        by_category = {name: month_spent.get(name, Decimal(0)) for name in trend_categories}
        rest = sum((v for k, v in month_spent.items() if k not in trend_categories), Decimal(0))
        if rest or len(ranking) > len(trend_categories):
            by_category[OTHER] = rest
        trend.append(
            TrendMonth(
                month=trend_month,
                total=_sum(receipts_df[receipts_df["month"] == trend_month]["total"]),
                by_category=by_category,
            )
        )
    if any(OTHER in t.by_category for t in trend):
        trend_categories = [*trend_categories, OTHER]
        for t in trend:
            t.by_category.setdefault(OTHER, Decimal(0))

    # --- top stores and items (this period) -------------------------------------------
    top_stores: list[StoreSpending] = []
    if not current.empty:
        grouped = current.groupby("store").agg(total=("total", "sum"), visits=("receipt_id", "count"))
        for store, row in grouped.iterrows():
            top_stores.append(
                StoreSpending(store=str(store), total=_dec(row["total"]), visits=int(row["visits"]))
            )
        top_stores.sort(key=lambda s: (-s.total, -s.visits, s.store))

    top_items: list[ItemFrequency] = []
    current_lines = _window(lines, month, last_day)
    current_lines = current_lines[(current_lines["key"] != "") & (current_lines["category"] != TAX_CATEGORY)]
    if not current_lines.empty:
        grouped_items = current_lines.groupby("key").agg(
            name=("name", "first"), times=("receipt_id", "count"), total=("amount", "sum")
        )
        for _, row in grouped_items.iterrows():
            top_items.append(
                ItemFrequency(name=str(row["name"]), times=int(row["times"]), total=_dec(row["total"]))
            )
        top_items.sort(key=lambda i: (-i.times, -i.total, i.name))

    changes = _changes(categories, average_total, receipts_df, current, month)

    return Summary(
        currency=currency,
        currencies=currencies,
        period=period,
        total=total,
        receipt_count=len(current),
        previous_total=previous_total,
        average_total=average_total,
        categories=categories,
        trend=trend,
        trend_categories=trend_categories,
        top_stores=top_stores[:TOP_LIST_SIZE],
        top_items=top_items[:TOP_LIST_SIZE],
        changes=changes,
    )


def _changes(
    categories: list[CategorySpending],
    average_total: Decimal | None,
    receipts_df: pd.DataFrame,
    current: pd.DataFrame,
    month: str,
) -> list[Change]:
    """Plain-language patterns: categories well above/below usual, new categories, new stores."""
    if average_total is None:
        return []  # nothing to compare with yet

    changes: list[Change] = []
    threshold = average_total * CHANGE_SHARE_OF_SPENDING
    total = sum((c.total for c in categories), Decimal(0))
    for category in categories:
        if category.category in (TAX_CATEGORY, NOT_ITEMIZED) or category.average_total is None:
            continue
        average, spent = category.average_total, category.total
        difference = spent - average
        if average == 0:
            if spent > 0 and total and spent / total >= NEW_CATEGORY_SHARE:
                changes.append(Change(kind="category_new", category=category.category, amount=spent))
        elif abs(difference) >= threshold and abs(difference) / average >= CHANGE_RATIO:
            kind = "category_up" if difference > 0 else "category_down"
            changes.append(Change(kind=kind, category=category.category, amount=spent, baseline=average))
    changes.sort(key=lambda c: -abs(c.amount - (c.baseline or 0)))

    seen_before = set(receipts_df[receipts_df["month"] < month]["store"].str.casefold())
    new_stores = (
        current[~current["store"].str.casefold().isin(seen_before)].groupby("store")["total"].sum()
        if not current.empty
        else pd.Series(dtype=object)
    )
    ranked = sorted(((str(store), _dec(spent)) for store, spent in new_stores.items()), key=lambda kv: -kv[1])
    for store, spent in ranked[:MAX_NEW_STORES]:
        if store:
            changes.append(Change(kind="new_store", store=store, amount=spent))
    return changes[:MAX_CHANGES]
