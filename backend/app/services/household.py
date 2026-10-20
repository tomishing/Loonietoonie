"""Compared with similar households: the user's monthly spending per category next to Statistics
Canada's average for households like theirs (Survey of Household Spending, app/reference).

The survey publishes averages by province (all households) and by household type (Canada only),
not by both, so a household type in a province is estimated as the Canadian average for that type
times the province's ratio to Canada. Amounts are brought to the latest year's prices with the
province's Consumer Price Index. Only categories the app sees well are compared, and the user's
side is the average of the last full months, so a month in progress doesn't count.
"""

import json
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from functools import cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.services.summary import build_frames, shift_month
from app.storage.models import Receipt

DATA_FILE = Path(__file__).resolve().parent.parent / "reference" / "household_spending.json"
COMPARE_MONTHS = 3
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
    yours: Decimal  # the user's monthly average
    typical: Decimal  # similar households' monthly average, at current prices
    difference: Decimal  # yours / typical - 1 (e.g. 0.20 = 20% more)


class Comparison(BaseModel):
    # "ok", "not_set" (no household yet), "no_history" (no full month yet), or "currency" (not CAD)
    status: str
    household: str = ""
    region: str = ""
    months: list[str] = []
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


def typical_monthly(household_type: str, province: str) -> dict[str, Decimal]:
    """Similar households' monthly spending per compared category key, at current prices."""
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
        result[key] = (annual * factor / 12).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return result


def compare(receipts: list[Receipt], settings: dict[str, str], today: date) -> Comparison:
    view = settings_view(settings)
    if not view.household_type or not view.province:
        return Comparison(status="not_set")
    data = reference()
    frames = build_frames([r for r in receipts if r.currency == CURRENCY])
    lines = frames.lines
    if lines.empty:
        return Comparison(status="no_history")
    first_month = str(frames.receipts["month"].min())
    this_month = today.strftime("%Y-%m")
    months = [
        m for m in (shift_month(this_month, -o) for o in range(COMPARE_MONTHS, 0, -1)) if m >= first_month
    ]
    if not months:
        return Comparison(status="no_history")
    in_months = lines[lines["month"].isin(months)]
    spent: dict[str, Decimal] = {}
    for category, amount in zip(in_months["category"], in_months["amount"], strict=True):
        name = str(category).casefold()
        spent[name] = spent.get(name, Decimal(0)) + amount

    typical = typical_monthly(view.household_type, view.province)
    rows: list[ComparisonRow] = []
    for category in data["categories"]:
        total = sum(
            (spent.get(name.casefold(), Decimal(0)) for name in category["app_categories"]), Decimal(0)
        )
        expected = typical.get(category["key"])
        if total <= 0 or not expected:
            continue  # nothing spent here (e.g. no pets) or no survey figure
        yours = (total / len(months)).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        difference = (yours / expected - 1).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        rows.append(
            ComparisonRow(category=category["label"], yours=yours, typical=expected, difference=difference)
        )
    return Comparison(
        status="ok" if rows else "no_history",
        household=HOUSEHOLD_TYPES[view.household_type],
        region=data["provinces"][view.province]["name"],
        months=months,
        survey_year=data["survey_year"],
        price_year=data["price_year"],
        rows=rows,
        source=data["source"],
    )
