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
        D(couple)
        * D(bc["annual"]["groceries"])
        / D(canada["annual"]["groceries"])
        * D(bc["price_factor"])
        / 12
    )

    assert household.typical_monthly("couple_children", "BC")["groceries"] == expected.quantize(D(1))
    # Canada as a whole: the household type's own average.
    canada_couple = D(couple) * D(canada["price_factor"]) / 12
    assert household.typical_monthly("couple_children", "CA")["groceries"] == canada_couple.quantize(D(1))


def test_compare_uses_the_last_full_months_and_categories_the_household_has() -> None:
    receipts = [
        receipt("2026-07-05", "FreshCo", [("Milk", "600", "Groceries"), ("Movie", "40", "Entertainment")]),
        receipt("2026-08-05", "FreshCo", [("Eggs", "900", "Groceries"), ("Paint", "60", "Hobbies")]),
        receipt("2026-09-05", "FreshCo", [("Rice", "900", "Groceries")]),
        receipt("2026-10-02", "FreshCo", [("Bread", "5000", "Groceries")]),  # month in progress: left out
        receipt("2026-09-06", "Tokyo", [("Snacks", "3000", "Groceries")], currency="JPY"),  # not CAD
    ]

    result = household.compare(receipts, FAMILY, TODAY)

    assert (result.status, result.household, result.region) == (
        "ok",
        "Couple with children",
        "British Columbia",
    )
    assert result.months == ["2026-07", "2026-08", "2026-09"]
    typical = household.typical_monthly("couple_children", "BC")
    rows = {r.category: r for r in result.rows}
    assert set(rows) == {"Groceries", "Entertainment & Hobbies"}  # no pets, no clothing spending
    assert rows["Groceries"].yours == D(800)
    assert rows["Groceries"].typical == typical["groceries"]
    assert rows["Groceries"].difference == (D(800) / typical["groceries"] - 1).quantize(D("0.01"))
    assert rows["Entertainment & Hobbies"].yours == D(33)  # (40 + 60) / 3, Entertainment and Hobbies together


def test_compare_counts_only_months_since_the_first_purchase() -> None:
    result = household.compare(
        [receipt("2026-09-10", "FreshCo", [("Milk", "500", "Groceries")])], FAMILY, TODAY
    )

    assert result.months == ["2026-09"]
    assert result.rows[0].yours == D(500)


@pytest.mark.parametrize(
    ("receipts", "settings", "status"),
    [
        ([receipt("2026-09-10", "FreshCo", [("Milk", "5", "Groceries")])], {}, "not_set"),
        (
            [receipt("2026-09-10", "FreshCo", [("Milk", "5", "Groceries")])],
            {"household_type": "couple"},
            "not_set",
        ),
        ([receipt("2026-10-10", "FreshCo", [("Milk", "5", "Groceries")])], FAMILY, "no_history"),
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
