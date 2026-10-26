from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.auth.dependencies import get_current_user
from app.auth.session import SessionData
from app.bank.plaid import PersonalFinanceCategory, PlaidTransaction
from app.config import get_settings
from app.main import app
from app.services import bank, bank_categories
from app.services.bank_categories import CategoryChoiceError, set_category
from app.services.names import clean_bank_text, display_name, merchant_key
from app.sheets.setup import ensure_spreadsheet
from app.storage.dependencies import get_repository
from app.storage.models import BankTransactionFilter, MerchantRule
from app.storage.sheets_repository import SheetsRepository, TabReadCache
from tests.fakes import FakeSheetsApi
from tests.test_auth import make_settings


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("RETAIL PURCHASE 000024305550 SUZUYA JAPANESE", "Suzuya Japanese"),
        ("SUZUYA JAPANESE MARKET BURNABY, BC", "Suzuya Japanese Market Burnaby, BC"),
        ("POS PURCHASE STARBUCKS #1234", "Starbucks"),
        ("Interac purchase - Metro", "Metro"),  # words in mixed case are kept as the bank wrote them
        ("000024305550", "000024305550"),  # nothing left: keep the original
        ("CARRS #1811 WASILLA, AK 13.32 USD @ 1.421171", "Carrs Wasilla, AK"),
        ("RETAIL PURCHASE 530417366227 JAK'S BEER WINE", "Jak's Beer Wine"),
        ("A&W RESTAURANT (KINGSWAY) BURNABY, BC", "A&W Restaurant (Kingsway) Burnaby, BC"),
        ("INTERNET TRANSFER 000000221686 TO CARD 4505********0639", "Internet Transfer To Card"),
        ("RETAIL PURCHASE 3HLUY9U50000 Vancouver Metro", "Vancouver Metro"),
        ("GROWMUSHROO 2503276931, BC", "Growmushroo, BC"),
        ("GARYS KETTLE CORN Eagle Bay, BC", "Garys Kettle Corn Eagle Bay, BC"),
        ("Amazon.ca*8S9F86GI3 866-216-1072, ON", "Amazon.ca, ON"),
        ("INTL VISA DEB RETAIL PURCHASE IHERB IHERB.COM", "Iherb Iherb.Com"),
    ],
)
def test_clean_bank_text(text: str, expected: str) -> None:
    assert clean_bank_text(text) == expected


@pytest.mark.parametrize(
    ("merchant", "description", "expected"),
    [
        ("Suzuyo America Inc.", "SUZUYA JAPANESE MARKET BURNABY, BC", "Suzuya Japanese Market Burnaby, BC"),
        ("Sushiya Japanese Restaurant", "RETAIL PURCHASE 000024305550 SUZUYA JAPANESE", "Suzuya Japanese"),
        ("Starbucks", "STARBUCKS #1234 VANCOUVER BC", "Starbucks"),
        ("The Home Depot", "THE HOME DEPOT #7000", "The Home Depot"),
        ("McDonald's", "", "McDonald's"),
        ("A&W Restaurants", "A&W RESTAURANT (KINGSWAY) BURNABY, BC", "A&W Restaurants"),
        ("Superior Vending", "RETAIL PURCHASE 527409985785 COMPASS VENDING", "Compass Vending"),
        ("City of Vancouver", "CITY BBY ROSEMARY BROWN ( BURNABY, BC", "City Bby Rosemary Brown Burnaby, BC"),
        ("City of Vancouver", "CITY OF VANCOUVER PARKING", "City of Vancouver"),
        ("Sushi Garden", "SUSHI GARDEN METROTOWN", "Sushi Garden"),
        ("Triple O Construction", "TRIPLE O # 570 KINGSWAY BURNABY, BC", "Triple O Kingsway Burnaby, BC"),
        ("Triple O Six", "RETAIL PURCHASE 001001001011 TRIPLE O'S #202", "Triple O's"),
        ("Compass Autos LLC", "VISA DEBIT RETAIL PURCHASE COMPASS AUTOLOA 609610197310", "Compass Autoloa"),
        ("Bath & Body Works", "RETAIL PURCHASE 607219291814 BATH BODY WOR", "Bath & Body Works"),  # cut short
        ("Super Saver Gas", "RETAIL PURCHASE 274001001004 SUPER SAVE GAS", "Super Saver Gas"),
        ("Shoppers Drug Mart", "SHOPPERS DRUG MART #2201 BURNABY, BC", "Shoppers Drug Mart"),
        ("Whistler's Inn", "EARLS WHISTLER Whistler, BC", "Earls Whistler Whistler, BC"),
        ("L M Hanna Co", "RETAIL PURCHASE 516820283245 HANNAM METROTOW", "Hannam Metrotow"),
        ("Save-On-Foods", "SAVE ON FOODS #2228 BURNABY, BC", "Save-On-Foods"),
        ("London Drug", "RETAIL PURCHASE 524316306168 LONDON DRUGS 06", "London Drug"),
        ("IKEA", "IKEA COQUITLAM COQUITLAM, BC", "IKEA"),
        ("A&W Restaurants", "A & W 340 VANCOUVER, BC", "A&W Restaurants"),
        ("Amazon", "Amazon.ca*8S9F86GI3 866-216-1072, ON", "Amazon"),
        ("Amazon", "AMAZON* 6C7XO5SE3 VANCOUVER, BC", "Amazon"),
        ("iHerb", "INTL VISA DEB RETAIL PURCHASE IHERB IHERB.COM", "iHerb"),
        ("", "UBER *TRIP", "Uber *Trip"),
    ],
)
def test_display_name_trusts_plaid_only_when_it_agrees(
    merchant: str, description: str, expected: str
) -> None:
    assert display_name(merchant, description) == expected


def test_merchant_key_groups_spellings_of_one_store() -> None:
    assert (
        merchant_key("Suzuya Japanese Market Burnaby, BC")
        == merchant_key("Suzuya Japanese")
        == "suzuya japanese"
    )
    assert merchant_key("Starbucks") != merchant_key("Suzuya Japanese")
    assert merchant_key("セブン-イレブン") == merchant_key("セブンイレブン")


# --- with storage ---------------------------------------------------------------


@pytest.fixture
def repository() -> SheetsRepository:
    api = FakeSheetsApi()
    return SheetsRepository(api, ensure_spreadsheet(api, "2026-10-16T00:00:00Z"), TabReadCache(ttl_seconds=0))


def plaid(
    transaction_id: str, name: str, merchant: str | None, detailed: str, day: int = 5
) -> PlaidTransaction:
    return PlaidTransaction(
        transaction_id=transaction_id,
        account_id="acc",
        amount=Decimal("25.00"),
        iso_currency_code="CAD",
        date=date(2026, 10, day),
        name=name,
        merchant_name=merchant,
        personal_finance_category=PersonalFinanceCategory(detailed=detailed),
    )


def test_remembered_category_applies_to_past_and_new_charges(repository: SheetsRepository) -> None:
    categories = repository.list_categories()
    rows = [
        plaid("a", "SUZUYA JAPANESE MARKET BURNABY, BC", "Suzuyo America Inc.", "GENERAL_MERCHANDISE_OTHER"),
        plaid(
            "b",
            "RETAIL PURCHASE 000024305550 SUZUYA JAPANESE",
            "Sushiya Japanese Restaurant",
            "FOOD_AND_DRINK_RESTAURANT",
        ),
        plaid("c", "STARBUCKS #1", "Starbucks", "FOOD_AND_DRINK_COFFEE"),
    ]
    repository.apply_bank_changes([bank.to_transaction(t, categories, "CAD") for t in rows], [])
    by_id = {t.transaction_id: t for t in repository.list_bank_transactions(BankTransactionFilter())}
    assert (by_id["b"].merchant, by_id["b"].category) == ("Suzuya Japanese", "Dining Out")  # Plaid's guess

    changed = set_category(repository, "b", "spending", "groceries", "", apply_to_merchant=True)

    assert changed == 2  # both Suzuya charges, not Starbucks
    by_id = {t.transaction_id: t for t in repository.list_bank_transactions(BankTransactionFilter())}
    assert [by_id[i].category for i in "abc"] == ["Groceries", "Groceries", "Dining Out"]
    assert repository.list_merchant_rules() == [
        MerchantRule(merchant="suzuya japanese", kind="spending", category="Groceries", subcategory="")
    ]
    # A new Suzuya charge arrives with Plaid's restaurant guess: the remembered category wins.
    rules = {r.merchant: r for r in repository.list_merchant_rules()}
    new = bank.to_transaction(
        plaid(
            "d",
            "RETAIL PURCHASE 000099999999 SUZUYA JAPANESE",
            "Sushiya Japanese Restaurant",
            "FOOD_AND_DRINK_RESTAURANT",
        ),
        categories,
        "CAD",
        rules,
    )
    assert (new.kind, new.category) == ("spending", "Groceries")


def test_one_charge_only_and_not_spending(repository: SheetsRepository) -> None:
    categories = repository.list_categories()
    rows = [
        plaid("a", "E-TRANSFER JOHN", None, "GENERAL_SERVICES_OTHER"),
        plaid("b", "E-TRANSFER JOHN", None, "GENERAL_SERVICES_OTHER"),
    ]
    repository.apply_bank_changes([bank.to_transaction(t, categories, "CAD") for t in rows], [])

    assert set_category(repository, "a", "transfer", "Other", "x", apply_to_merchant=False) == 1

    by_id = {t.transaction_id: t for t in repository.list_bank_transactions(BankTransactionFilter())}
    assert (by_id["a"].kind, by_id["a"].category, by_id["a"].subcategory) == ("transfer", "", "")
    assert by_id["b"].kind == "spending"
    assert repository.list_merchant_rules() == []
    with pytest.raises(CategoryChoiceError):
        set_category(repository, "a", "spending", "Groceries", "Not a subcategory", apply_to_merchant=False)


@pytest.fixture
def client(repository: SheetsRepository) -> Iterator[TestClient]:
    user = SessionData(sub="u", email="t@example.com", refresh_token="r", spreadsheet_id="s")
    app.dependency_overrides[get_settings] = lambda: make_settings()
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_repository] = lambda: repository
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_category_endpoint(client: TestClient, repository: SheetsRepository) -> None:
    repository.apply_bank_changes(
        [
            bank.to_transaction(
                plaid("a", "SUZUYA JAPANESE MARKET", None, "GENERAL_MERCHANDISE_OTHER"),
                repository.list_categories(),
                "CAD",
            )
        ],
        [],
    )
    url = "/api/bank/transactions/a/category"

    assert client.put(url, json={"category": "Groceries", "subcategory": "Pantry"}).json() == {"changed": 1}
    assert client.put(url, json={"category": "Nope"}).status_code == 422
    assert (
        client.put("/api/bank/transactions/zzz/category", json={"category": "Groceries"}).status_code == 404
    )


@pytest.mark.parametrize(
    ("merchant", "description", "confidence", "expected"),
    [
        # Known chains the bank abbreviates: trusted at VERY_HIGH, not otherwise.
        (
            "Real Canadian Superstore",
            "RETAIL PURCHASE 001001001018 REAL CDN SUPERS",
            "VERY_HIGH",
            "Real Canadian Superstore",
        ),
        (
            "Real Canadian Superstore",
            "RETAIL PURCHASE 001001001018 REAL CDN SUPERS",
            "HIGH",
            "Real Cdn Supers",
        ),
        ("Esso", "KELOWNA ESSO KELOWNA, BC", "VERY_HIGH", "Esso"),
        (
            "Amazon Kindle",
            "Kindle Svcs*B96R85DW0 888-802-3080, WA 29.68 USD @ 1.403301",
            "VERY_HIGH",
            "Amazon Kindle",
        ),
        # Even VERY_HIGH needs one shared word: NYF is New York Fries, not New York Life.
        ("New York Life", "NYF #3304 SURREY, BC", "VERY_HIGH", "Nyf Surrey, BC"),
        # HIGH is where Plaid's wrong guesses were, so it gets the full check.
        (
            "Suzuyo America Inc.",
            "SUZUYA JAPANESE MARKET BURNABY, BC",
            "HIGH",
            "Suzuya Japanese Market Burnaby, BC",
        ),
    ],
)
def test_very_high_confidence_needs_less_agreement(
    merchant: str, description: str, confidence: str, expected: str
) -> None:
    assert display_name(merchant, description, confidence) == expected


def test_merchant_confidence_is_read_from_counterparties() -> None:
    t = PlaidTransaction.model_validate(
        {
            "transaction_id": "t",
            "account_id": "a",
            "amount": 1,
            "date": "2026-10-01",
            "counterparties": [
                {"name": "Visa", "type": "payment_network", "confidence_level": "LOW"},
                {"name": "Esso", "type": "merchant", "confidence_level": "VERY_HIGH"},
            ],
        }
    )
    assert t.merchant_confidence == "VERY_HIGH"
    assert (
        PlaidTransaction(
            transaction_id="t", account_id="a", amount=Decimal(1), date=date(2026, 10, 1)
        ).merchant_confidence
        == ""
    )


def test_remembered_category_is_found_by_bank_text_or_plaids_name() -> None:
    rule = MerchantRule(merchant="skechers usa", kind="spending", category="Clothing", subcategory="")
    by_plaid = {"skechers usa": rule}
    by_text = {"skechers burnaby": rule}

    assert (
        bank_categories.find_rule(
            by_plaid, "Skechers Burnaby, BC", "Skechers Burnaby, BC", "SKECHERS #1 BURNABY", "Skechers USA"
        )
        is rule
    )
    assert (
        bank_categories.find_rule(by_text, "Sketchers Inc", "Sketchers Inc", "SKECHERS #1 BURNABY, BC")
        is rule
    )
    assert bank_categories.find_rule(by_text, "Starbucks", "Starbucks", "STARBUCKS #9") is None
