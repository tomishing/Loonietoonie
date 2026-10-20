"""Build app/reference/household_spending.json from Statistics Canada's public tables.

Run from backend/ when a new Survey of Household Spending (every two years) or a new year of the
Consumer Price Index comes out:

    .venv/bin/python scripts/build_household_spending.py

Sources (Statistics Canada Open Licence):
- 11-10-0222-01 Household spending, Canada, regions and provinces (average per household)
- 11-10-0224-01 Household spending by household type (Canada)
- 18-10-0005-01 Consumer Price Index, annual average (all-items, by province)
"""

import csv
import io
import json
import sys
import urllib.request
import zipfile
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

TABLES = {"regions": "11100222", "types": "11100224", "cpi": "18100005"}
URL = "https://www150.statcan.gc.ca/n1/tbl/csv/{table}-eng.zip"
OUTPUT = Path(__file__).resolve().parent.parent / "app" / "reference" / "household_spending.json"
CATEGORY = "Household expenditures, summary-level categories"

PROVINCES = {
    "NL": "Newfoundland and Labrador",
    "PE": "Prince Edward Island",
    "NS": "Nova Scotia",
    "NB": "New Brunswick",
    "QC": "Quebec",
    "ON": "Ontario",
    "MB": "Manitoba",
    "SK": "Saskatchewan",
    "AB": "Alberta",
    "BC": "British Columbia",
    "CA": "Canada",
}
HOUSEHOLD_TYPES = {
    "one_person": "One person households",
    "couple": "Couples without children",
    "couple_children": "Couples with children",
    "lone_parent": "Lone-parent households with no additional persons",
    "couple_others": "Couples with other related or unrelated persons",
    "other": "Other households with related or unrelated persons",
    "all": "All classes",
}
# App categories compared with the survey: survey lines added up, minus lines that belong to other
# app categories (e.g. package trips are Travel, computers and TVs are Household > Electronics).
COMPARED = [
    # Rent, or mortgage payments, repairs, condo fees, property taxes, and insurance for the home
    # (water, fuel, and electricity are Utilities; second homes and hotels aren't Housing).
    ("housing", "Housing", ["Housing"], ["Rented living quarters", "Owned living quarters"], []),
    ("groceries", "Groceries", ["Groceries"], ["Food purchased from stores"], []),
    ("dining_out", "Dining Out", ["Dining Out"], ["Food purchased from restaurants"], []),
    (
        "transportation",
        "Transportation",
        ["Transportation"],
        [
            "Automobile, van and truck operations",
            "Fees for leased automobiles, vans and trucks",
            "City or commuter bus, subway, streetcar and commuter train",
            "Taxi (including tips)",
            "Peer-to-peer ride services",
            "Other local passenger transportation",
        ],
        [],
    ),
    (
        "utilities",
        "Utilities",
        ["Utilities"],
        ["Water, fuel and electricity for principal accommodation", "Communications"],
        [],
    ),
    (
        "household",
        "Household",
        ["Household"],
        [
            "Household furnishings and equipment",
            "Household cleaning supplies and equipment",
            "Paper, plastic and foil supplies",
            "Computer equipment and supplies",
            "Home entertainment equipment",
        ],
        [],
    ),
    ("clothing", "Clothing", ["Clothing"], ["Clothing and accessories"], []),
    (
        "entertainment",
        "Entertainment & Hobbies",
        ["Entertainment", "Hobbies"],
        ["Recreation", "Reading materials and other printed matter"],
        [
            "Package trips",
            "Purchase of recreational vehicles",
            "Computer equipment and supplies",
            "Home entertainment equipment",
        ],
    ),
    ("personal_care", "Personal Care", ["Personal Care"], ["Personal care"], []),
    ("health", "Health", ["Health"], ["Health care"], ["Private health insurance plan premiums"]),
    ("pets", "Pets", ["Pets"], ["Pet expenses"], []),
    ("education", "Education", ["Education"], ["Education"], []),
]


def download(table: str) -> list[dict[str, str]]:
    with urllib.request.urlopen(URL.format(table=table), timeout=120) as response:
        archive = zipfile.ZipFile(io.BytesIO(response.read()))
    with archive.open(f"{table}.csv") as data:
        return list(csv.DictReader(io.TextIOWrapper(data, encoding="utf-8-sig")))


def amounts(rows: list[dict[str, str]], year: str, key: str, value: str) -> dict[str, Decimal]:
    return {
        r[CATEGORY]: Decimal(r["VALUE"])
        for r in rows
        if r["REF_DATE"] == year and r[key] == value and r["VALUE"].strip()
    }


def compared(lines: dict[str, Decimal]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, _, _, add, subtract in COMPARED:
        if not all(name in lines for name in add):
            continue  # suppressed by Statistics Canada for this group
        total = sum((lines[n] for n in add), Decimal(0)) - sum(
            (lines.get(n, Decimal(0)) for n in subtract), Decimal(0)
        )
        result[key] = str(total.quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return result


def main() -> None:
    regions, types, cpi = (download(TABLES[name]) for name in ("regions", "types", "cpi"))
    survey_year = max(r["REF_DATE"] for r in regions)
    all_items = [r for r in cpi if r["Products and product groups"] == "All-items" and r["VALUE"].strip()]
    price_year = max(r["REF_DATE"] for r in all_items)
    index = {(r["GEO"], r["REF_DATE"]): Decimal(r["VALUE"]) for r in all_items}

    provinces = {}
    for code, name in PROVINCES.items():
        factor = index[(name, price_year)] / index[(name, survey_year)]
        provinces[code] = {
            "name": name,
            "price_factor": str(factor.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)),
            "annual": compared(amounts(regions, survey_year, "GEO", name)),
        }
    household_types = {
        code: {"survey": name, "annual": compared(amounts(types, survey_year, "Household type", name))}
        for code, name in HOUSEHOLD_TYPES.items()
    }
    data = {
        "source": (
            "Statistics Canada, Survey of Household Spending (tables 11-10-0222-01 and 11-10-0224-01) "
            "and Consumer Price Index (table 18-10-0005-01). Statistics Canada Open Licence."
        ),
        "survey_year": int(survey_year),
        "price_year": int(price_year),
        "categories": [
            {"key": key, "label": label, "app_categories": app} for key, label, app, _, _ in COMPARED
        ],
        "provinces": provinces,
        "household_types": household_types,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT} (survey {survey_year}, prices {price_year})", file=sys.stderr)


if __name__ == "__main__":
    main()
