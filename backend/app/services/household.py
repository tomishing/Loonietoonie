"""Compared with similar households: the user's monthly spending per category next to Statistics
Canada's average for households like theirs (Survey of Household Spending, app/reference).

The survey publishes averages by province (all households) and by household type (Canada only),
not by both, so a household type in a province is estimated as the Canadian average for that type
times the province's ratio to Canada. Amounts are brought to the latest year's prices with the
province's Consumer Price Index. Only categories the app sees well are compared: what the user
spent since January 1 (or since their first purchase this year) next to the same share of a
typical year.
"""

import calendar
import json
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from functools import cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.services.summary import build_frames
from app.storage.models import Receipt

DATA_FILE = Path(__file__).resolve().parent.parent / "reference" / "household_spending.json"
HOUSEHOLD_TYPE_KEY = "household_type"
PROVINCE_KEY = "province"
CURRENCY = "CAD"  # the survey is in Canadian dollars

HOUSEHOLD_TYPES = {
    "one_person": "Living alone",
    "couple": "Couple without children",
    "couple_children": "Couple with children",
    "lone_parent": "Single parent with children",
    "couple_others": "Couple living with others (e.g. parents)",
    "other": "Other (roommates, relatives)",
}
PROVINCE_LABELS = {
    "AB": "Alberta",
    "BC": "British Columbia",
    "MB": "Manitoba",
    "NB": "New Brunswick",
    "NL": "Newfoundland and Labrador",
    "NS": "Nova Scotia",
    "ON": "Ontario",
    "PE": "Prince Edward Island",
    "QC": "Quebec",
    "SK": "Saskatchewan",
    "CA": "Territories or elsewhere (Canada average)",
}


class Option(BaseModel):
    value: str
    label: str


class Household(BaseModel):
    household_type: str  # a HOUSEHOLD_TYPES key, or "" when not set
    province: str  # a PROVINCE_LABELS key, or "" when not set


class HouseholdSettings(Household):
    household_types: list[Option]
    provinces: list[Option]


class ComparisonRow(BaseModel):
    category: str  # e.g. "Groceries", or "Entertainment & Hobbies" for both
    yours: Decimal  # what the user spent in the period
    typical: Decimal  # what similar households spend in a period this long, at current prices
    difference: Decimal  # yours / typical - 1 (e.g. 0.20 = 20% more)


class Comparison(BaseModel):
    # "ok", "not_set" (no household yet), or "no_history" (nothing to compare yet)
    status: str
    household: str = ""
    region: str = ""
    start: date | None = None  # January 1, or the first purchase this year when records start later
    end: date | None = None  # today
    survey_year: int = 0
    price_year: int = 0
    rows: list[ComparisonRow] = []
    source: str = ""


@cache
def reference() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    return data


def settings_view(settings: dict[str, str]) -> HouseholdSettings:
    household_type = settings.get(HOUSEHOLD_TYPE_KEY, "")
    province = settings.get(PROVINCE_KEY, "")
    return HouseholdSettings(
        household_type=household_type if household_type in HOUSEHOLD_TYPES else "",
        province=province if province in PROVINCE_LABELS else "",
        household_types=[Option(value=k, label=v) for k, v in HOUSEHOLD_TYPES.items()],
        provinces=[Option(value=k, label=v) for k, v in PROVINCE_LABELS.items()],
    )


def typical_annual(household_type: str, province: str) -> dict[str, Decimal]:
    """Similar households' yearly spending per compared category key, at current prices (unrounded)."""
    data = reference()
    canada = data["provinces"]["CA"]["annual"]
    region = data["provinces"][province]
    by_type = data["household_types"][household_type]["annual"]
    factor = Decimal(region["price_factor"])
    result: dict[str, Decimal] = {}
    for key, type_value in by_type.items():
        if key not in canada or key not in region["annual"] or Decimal(canada[key]) == 0:
            continue
        annual = Decimal(type_value) * Decimal(region["annual"][key]) / Decimal(canada[key])
        result[key] = annual * factor
    return result


def _spent_by_category(lines: Any) -> dict[str, Decimal]:
    spent: dict[str, Decimal] = {}
    for category, amount in zip(lines["category"], lines["amount"], strict=True):
        name = str(category).casefold()
        spent[name] = spent.get(name, Decimal(0)) + amount
    return spent


def _rows(spent: dict[str, Decimal], expected: dict[str, Decimal]) -> list[ComparisonRow]:
    """One row per compared category with spending."""
    rows: list[ComparisonRow] = []
    for category in reference()["categories"]:
        names = [name.casefold() for name in category["app_categories"]]
        total = sum((spent.get(name, Decimal(0)) for name in names), Decimal(0))
        typical = expected.get(category["key"])
        if total <= 0 or not typical:
            continue  # nothing spent here (e.g. no pets) or no survey figure
        yours = total.quantize(Decimal(1), rounding=ROUND_HALF_UP)
        difference = (yours / typical - 1).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        rows.append(
            ComparisonRow(category=category["label"], yours=yours, typical=typical, difference=difference)
        )
    return rows


def compare(receipts: list[Receipt], settings: dict[str, str], today: date) -> Comparison:
    view = settings_view(settings)
    if not view.household_type or not view.province:
        return Comparison(status="not_set")
    data = reference()
    frames = build_frames([r for r in receipts if r.currency == CURRENCY])
    lines = frames.lines
    if lines.empty:
        return Comparison(status="no_history")
    # Since January 1 (or the first purchase this year), next to the same share of a typical year.
    first_day = date.fromisoformat(str(frames.receipts["date"].min()))
    start = max(date(today.year, 1, 1), first_day)
    if start > today:
        return Comparison(status="no_history")
    in_period = lines[(lines["date"] >= start.isoformat()) & (lines["date"] <= today.isoformat())]
    year_days = 366 if calendar.isleap(today.year) else 365
    share = Decimal((today - start).days + 1) / year_days
    expected = {
        key: (annual * share).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        for key, annual in typical_annual(view.household_type, view.province).items()
    }
    rows = _rows(_spent_by_category(in_period), expected)
    if not rows:
        return Comparison(status="no_history")
    return Comparison(
        status="ok",
        household=HOUSEHOLD_TYPES[view.household_type],
        region=data["provinces"][view.province]["name"],
        start=start,
        end=today,
        survey_year=data["survey_year"],
        price_year=data["price_year"],
        rows=rows,
        source=data["source"],
    )
