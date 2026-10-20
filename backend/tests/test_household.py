from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.auth.dependencies import get_current_user
from app.auth.session import SessionData
from app.config import get_settings
from app.main import app
from app.services import household
from app.sheets.setup import ensure_spreadsheet
from app.storage.dependencies import get_repository
from app.storage.sheets_repository import SheetsRepository, TabReadCache
from tests.fakes import FakeSheetsApi
from tests.test_auth import make_settings
from tests.test_summary import receipt

D = Decimal
TODAY = date(2026, 10, 20)
FAMILY = {"household_type": "couple_children", "province": "BC"}


def test_reference_data_is_current_and_complete() -> None:
    data = household.reference()
    assert data["survey_year"] >= 2023 and data["price_year"] > data["survey_year"]
    assert set(household.PROVINCE_LABELS) <= set(data["provinces"])
    assert set(household.HOUSEHOLD_TYPES) <= set(data["household_types"])
    keys = {c["key"] for c in data["categories"]}
    assert set(data["provinces"]["BC"]["annual"]) == keys


def test_a_household_type_in_a_province_scales_the_canadian_average() -> None:
    data = household.reference()
    canada, bc = data["provinces"]["CA"], data["provinces"]["BC"]
    couple = data["household_types"]["couple_children"]["annual"]["groceries"]
    expected = (
        D(couple) * D(bc["annual"]["groceries"]) / D(canada["annual"]["groceries"]) * D(bc["price_factor"])
    )

    assert household.typical_annual("couple_children", "BC")["groceries"] == expected
    # Canada as a whole: the household type's own average.
    assert household.typical_annual("couple_children", "CA")["groceries"] == D(couple) * D(
        canada["price_factor"]
    )


@pytest.mark.parametrize(
    ("receipts", "settings", "status"),
    [
        ([receipt("2026-09-10", "FreshCo", [("Milk", "5", "Groceries")])], {}, "not_set"),
        (
            [receipt("2026-09-10", "FreshCo", [("Milk", "5", "Groceries")])],
            {"household_type": "couple"},
            "not_set",
        ),
        ([], FAMILY, "no_history"),
    ],
)
def test_compare_explains_why_there_is_nothing_yet(
    receipts: list[object], settings: dict[str, str], status: str
) -> None:
    assert household.compare(receipts, settings, TODAY).status == status  # type: ignore[arg-type]


@pytest.fixture
def client() -> Iterator[tuple[TestClient, SheetsRepository]]:
    api = FakeSheetsApi()
    repository = SheetsRepository(
        api, ensure_spreadsheet(api, "2026-10-20T00:00:00Z"), TabReadCache(ttl_seconds=0)
    )
    user = SessionData(sub="u", email="t@example.com", refresh_token="r", spreadsheet_id="s")
    app.dependency_overrides[get_settings] = lambda: make_settings()
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_repository] = lambda: repository
    with TestClient(app) as http:
        yield http, repository
    app.dependency_overrides.clear()


def test_household_api(client: tuple[TestClient, SheetsRepository]) -> None:
    http, repository = client

    empty = http.get("/api/household").json()
    assert (empty["household_type"], empty["province"]) == ("", "")
    assert {"value": "couple_children", "label": "Couple with children"} in empty["household_types"]
    assert http.get("/api/household/comparison").json()["status"] == "not_set"
    assert (
        http.put("/api/household", json={"household_type": "spaceship", "province": "BC"}).status_code == 422
    )

    saved = http.put("/api/household", json=FAMILY).json()
    assert (saved["household_type"], saved["province"]) == ("couple_children", "BC")
    assert repository.get_settings() == FAMILY
    moved = http.put("/api/household", json={"household_type": "couple_children", "province": "ON"}).json()
    assert moved["province"] == "ON"  # replaced, not added twice
    assert repository.get_settings()["province"] == "ON"

    cleared = http.delete("/api/household").json()
    assert (cleared["household_type"], cleared["province"]) == ("", "")
    assert http.get("/api/household/comparison").json()["status"] == "not_set"


def test_year_to_date_adds_up_since_january_first() -> None:
    receipts = [
        receipt("2025-12-30", "FreshCo", [("Milk", "999", "Groceries")]),  # last year: left out
        receipt("2026-01-01", "FreshCo", [("Eggs", "300", "Groceries")]),
        receipt("2026-06-15", "FreshCo", [("Rice", "400", "Groceries"), ("Shirt", "50", "Clothing")]),
        receipt("2026-10-20", "FreshCo", [("Bread", "5", "Groceries")]),  # today counts
    ]

    result = household.compare(receipts, FAMILY, TODAY)

    assert (result.status, result.household, result.region) == (
        "ok",
        "Couple with children",
        "British Columbia",
    )
    assert (result.start, result.end) == (date(2026, 1, 1), TODAY)
    rows = {r.category: r for r in result.rows}
    assert set(rows) == {"Groceries", "Clothing"}  # only categories with spending
    annual = household.typical_annual("couple_children", "BC")
    share = D((TODAY - date(2026, 1, 1)).days + 1) / 365  # 293 of 365 days
    assert rows["Groceries"].yours == D(705)
    assert rows["Groceries"].typical == (annual["groceries"] * share).quantize(D(1))
    assert rows["Clothing"].yours == D(50)


def test_year_to_date_starts_at_the_first_purchase_when_records_start_later() -> None:
    receipts = [
        receipt("2026-09-01", "FreshCo", [("Milk", "500", "Groceries")]),
        receipt("2026-10-02", "FreshCo", [("Eggs", "100", "Groceries")]),
    ]

    result = household.compare(receipts, FAMILY, TODAY)

    assert result.start == date(2026, 9, 1)
    days = (TODAY - date(2026, 9, 1)).days + 1
    expected = (household.typical_annual("couple_children", "BC")["groceries"] * days / 365).quantize(D(1))
    assert result.rows[0].typical == expected


def test_housing_and_entertainment_with_hobbies_are_compared() -> None:
    receipts = [
        receipt("2026-03-01", "231 Lane St", [("Rent", "2500", "Housing")]),
        receipt("2026-03-02", "Cineplex", [("Movie", "20", "Entertainment")]),
        receipt("2026-03-03", "Michaels", [("Paint", "30", "Hobbies")]),
        receipt("2026-03-04", "Petsmart", [("Food", "0", "Pets")]),  # nothing spent: no row
    ]

    rows = {r.category: r for r in household.compare(receipts, FAMILY, TODAY).rows}

    assert set(rows) == {"Housing", "Entertainment & Hobbies"}
    assert rows["Housing"].yours == D(2500)
    assert rows["Entertainment & Hobbies"].yours == D(50)
