import base64
import os
import sqlite3
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.auth.dependencies import get_current_user
from app.auth.session import SessionData
from app.bank.categories import map_category
from app.bank.dependencies import get_bank_provider, get_token_store
from app.bank.plaid import (
    PersonalFinanceCategory,
    PlaidAccount,
    PlaidClient,
    PlaidError,
    PlaidTransaction,
    SyncPage,
)
from app.bank.token_store import SqliteTokenStore, TokenStoreError, parse_key
from app.config import get_settings
from app.main import app
from app.services import bank as service
from app.sheets.setup import ensure_spreadsheet
from app.storage.dependencies import get_repository
from app.storage.models import BankTransactionFilter, Category
from app.storage.repository import StorageError
from app.storage.sheets_repository import SheetsRepository, TabReadCache
from tests.fakes import FakeSheetsApi
from tests.test_auth import make_settings

NOW = "2026-10-15T12:00:00Z"
USER = "google-sub-1"
CATEGORIES = [
    Category(name="Travel", icon="", subcategories=["Flights", "Lodging"]),
    Category(name="Hobbies", icon="", subcategories=["Sports & Fitness"]),
    Category(name="Education", icon="", subcategories=["Tuition & Courses"]),
    Category(name="Kids", icon="", subcategories=["School Supplies"]),
    Category(name="Household", icon="", subcategories=["Electronics & Appliances"]),
    Category(name="Groceries", icon="", subcategories=["Drinks"]),
    Category(name="Dining Out", icon="", subcategories=["Restaurants", "Coffee & Cafés"]),
    Category(name="Transportation", icon="", subcategories=["Fuel"]),
    Category(name="Other", icon=""),
]


def key() -> bytes:
    return os.urandom(32)


def plaid_tx(
    transaction_id: str,
    amount: str,
    detailed: str = "FOOD_AND_DRINK_GROCERIES",
    day: int = 5,
    pending: bool = False,
    name: str = "FRESHCO #123",
    authorized_day: int | None = None,
) -> PlaidTransaction:
    return PlaidTransaction(
        transaction_id=transaction_id,
        account_id="acc-1",
        amount=Decimal(amount),
        iso_currency_code="CAD",
        date=date(2026, 10, day),
        authorized_date=date(2026, 10, authorized_day) if authorized_day else None,
        name=name,
        merchant_name="FreshCo",
        pending=pending,
        personal_finance_category=PersonalFinanceCategory(detailed=detailed),  # primary derived from it
    )


class FakeProvider:
    def __init__(self) -> None:
        self.pages: dict[str, list[SyncPage]] = {}  # cursor → pages returned from it, in order
        self.errors: list[PlaidError] = []  # raised (once each) before the next sync call
        self.calls: list[tuple[str, str]] = []
        self.removed: list[str] = []

    def create_link_token(self, user_id: str, access_token: str | None = None) -> str:
        return f"link-update-{access_token}" if access_token else f"link-sandbox-{user_id}"

    def exchange_public_token(self, public_token: str) -> tuple[str, str]:
        if public_token == "bad":
            raise PlaidError("INVALID_PUBLIC_TOKEN", "bad token", 400)
        return "access-sandbox-secret", "item-1"

    def get_institution(self, access_token: str) -> tuple[str, str]:
        return "ins_1", "First Platypus Bank"

    def get_accounts(self, access_token: str) -> list[PlaidAccount]:
        return [
            PlaidAccount(
                account_id="acc-1",
                name="Plaid Credit Card",
                mask="3333",
                type="credit",
                subtype="credit card",
            )
        ]

    def sync_transactions(self, access_token: str, cursor: str) -> SyncPage:
        self.calls.append(("sync", cursor))
        if self.errors:
            raise self.errors.pop(0)
        queue = self.pages.get(cursor, [])
        return queue.pop(0) if queue else SyncPage(next_cursor=cursor or "c0", has_more=False)

    def remove_item(self, access_token: str) -> None:
        self.removed.append(access_token)

    def refresh_transactions(self, access_token: str) -> None:
        self.calls.append(("refresh", access_token))

    def bank_updated_at(self, access_token: str) -> str:
        return "2026-10-15T19:01:58Z"


@pytest.fixture
def store(tmp_path: Path) -> SqliteTokenStore:
    return SqliteTokenStore(tmp_path / "tokens.sqlite3", key())


@pytest.fixture
def repository() -> SheetsRepository:
    api = FakeSheetsApi()
    return SheetsRepository(api, ensure_spreadsheet(api, NOW), TabReadCache(ttl_seconds=0))


# --- token store ------------------------------------------------------------------


def test_tokens_are_encrypted_at_rest_and_bound_to_their_user(tmp_path: Path) -> None:
    path = tmp_path / "tokens.sqlite3"
    store = SqliteTokenStore(path, key())
    store.add_item(USER, "item-1", "access-sandbox-secret", "ins_1", "Bank", NOW)

    assert [i.access_token for i in store.list_items(USER)] == ["access-sandbox-secret"]
    assert store.list_items("someone-else") == []
    assert b"access-sandbox-secret" not in path.read_bytes()

    # A token row moved to another user can't be decrypted (the IDs are authenticated data).
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE plaid_items SET user_id = 'intruder'")
    with pytest.raises(TokenStoreError):
        store.list_items("intruder")


def test_wrong_key_cannot_read_tokens(tmp_path: Path) -> None:
    path = tmp_path / "tokens.sqlite3"
    SqliteTokenStore(path, key()).add_item(USER, "item-1", "secret", "", "", NOW)

    with pytest.raises(TokenStoreError):
        SqliteTokenStore(path, key()).list_items(USER)


def test_cursor_and_delete(store: SqliteTokenStore) -> None:
    store.add_item(USER, "item-1", "secret", "", "Bank", NOW)
    store.save_cursor(USER, "item-1", "cursor-9", NOW)

    assert (store.list_items(USER)[0].cursor, store.list_items(USER)[0].last_synced_at) == ("cursor-9", NOW)
    assert store.delete_item("someone-else", "item-1") is False
    assert store.delete_item(USER, "item-1") is True
    assert store.list_items(USER) == []


@pytest.mark.parametrize("value", ["not base64 !!", base64.urlsafe_b64encode(b"short").decode()])
def test_bad_keys_are_rejected(value: str) -> None:
    with pytest.raises(TokenStoreError):
        parse_key(value)


def test_key_without_padding_is_accepted() -> None:
    raw = os.urandom(32)
    assert parse_key(base64.urlsafe_b64encode(raw).decode().rstrip("=")) == raw


# --- categories --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("primary", "detailed", "expected"),
    [
        ("FOOD_AND_DRINK", "FOOD_AND_DRINK_COFFEE", ("spending", "Dining Out", "Coffee & Cafés")),
        ("FOOD_AND_DRINK", "FOOD_AND_DRINK_GROCERIES", ("spending", "Groceries", "")),
        ("TRANSPORTATION", "TRANSPORTATION_GAS", ("spending", "Transportation", "Fuel")),
        (
            "TRANSPORTATION",
            "TRANSPORTATION_PARKING",
            ("spending", "Transportation", ""),
        ),  # no such subcategory
        ("MEDICAL", "MEDICAL_DENTAL_CARE", ("spending", "Other", "")),  # user has no Health category
        ("LOAN_PAYMENTS", "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT", ("transfer", "", "")),
        ("TRANSFER_OUT", "TRANSFER_OUT_ACCOUNT_TRANSFER", ("transfer", "", "")),
        ("INCOME", "INCOME_WAGES", ("income", "", "")),
        ("", "TRANSFER_IN_DEPOSIT", ("transfer", "", "")),  # primary missing: taken from detailed
        ("TRAVEL", "TRAVEL_FLIGHTS", ("spending", "Travel", "Flights")),
        (
            "PERSONAL_CARE",
            "PERSONAL_CARE_GYMS_AND_FITNESS_CENTERS",
            ("spending", "Hobbies", "Sports & Fitness"),
        ),
        ("GENERAL_SERVICES", "GENERAL_SERVICES_EDUCATION", ("spending", "Education", "Tuition & Courses")),
        (
            "GENERAL_MERCHANDISE",
            "GENERAL_MERCHANDISE_ELECTRONICS",
            ("spending", "Household", "Electronics & Appliances"),
        ),
        ("TRAVEL", "TRAVEL_RENTAL_CARS", ("spending", "Travel", "")),  # no Car Rental in this list
        ("", "", ("spending", "Other", "")),
    ],
)
def test_plaid_categories_map_to_the_users_categories(
    primary: str, detailed: str, expected: tuple[str, str, str]
) -> None:
    mapped = map_category(primary, detailed, CATEGORIES)
    assert (mapped.kind, mapped.category, mapped.subcategory) == expected


# --- sync service ----------------------------------------------------------------


def link(
    provider: FakeProvider, store: SqliteTokenStore, repository: SheetsRepository
) -> service.ItemSyncResult:
    return service.link_item(provider, store, repository, USER, "public-token", CATEGORIES, "CAD", NOW)


def test_link_saves_token_accounts_and_first_import(
    store: SqliteTokenStore, repository: SheetsRepository
) -> None:
    provider = FakeProvider()
    provider.pages[""] = [SyncPage(added=[plaid_tx("t1", "12.50")], next_cursor="c1", has_more=True)]
    provider.pages["c1"] = [
        SyncPage(
            added=[plaid_tx("t2", "-3.00", "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT")],
            next_cursor="c2",
            has_more=False,
        )
    ]

    result = link(provider, store, repository)

    assert (result.status, result.institution, result.changes.added) == ("ok", "First Platypus Bank", 2)
    assert store.list_items(USER)[0].cursor == "c2"
    [account] = repository.list_bank_accounts()
    assert (account.institution, account.mask, account.type) == ("First Platypus Bank", "3333", "credit")
    t2, t1 = repository.list_bank_transactions(BankTransactionFilter())
    assert (t1.amount, t1.kind, t1.category, t1.merchant) == (
        Decimal("12.50"),
        "spending",
        "Groceries",
        "FreshCo",
    )
    assert (t2.kind, t2.category) == ("transfer", "")


def test_sync_updates_keeps_user_edits_and_soft_deletes(
    store: SqliteTokenStore, repository: SheetsRepository
) -> None:
    provider = FakeProvider()
    provider.pages[""] = [
        SyncPage(added=[plaid_tx("t1", "12.50", pending=True)], next_cursor="c1", has_more=False)
    ]
    link(provider, store, repository)
    # The user moves the charge to Dining Out in the spreadsheet.
    api = repository._api  # type: ignore[attr-defined]
    rows = api.sheet(repository._spreadsheet_id, "bank_transactions").rows  # type: ignore[attr-defined]
    rows[1][rows[0].index("category")] = "Dining Out"
    provider.pages["c1"] = [
        SyncPage(
            added=[plaid_tx("t3", "4.00", "FOOD_AND_DRINK_COFFEE")],
            modified=[plaid_tx("t1", "13.00", pending=False)],
            next_cursor="c2",
            has_more=False,
        )
    ]

    result = service.sync_all(provider, store, repository, USER, CATEGORIES, "CAD", NOW)

    assert result.items[0].changes.model_dump() == {"added": 1, "modified": 1, "removed": 0}
    t1 = next(
        t for t in repository.list_bank_transactions(BankTransactionFilter()) if t.transaction_id == "t1"
    )
    assert (t1.amount, t1.pending, t1.category) == (Decimal("13.00"), False, "Dining Out")

    provider.pages["c2"] = [SyncPage(removed=["t3"], next_cursor="c3", has_more=False)]
    service.sync_all(provider, store, repository, USER, CATEGORIES, "CAD", NOW)
    assert [t.transaction_id for t in repository.list_bank_transactions(BankTransactionFilter())] == ["t1"]


def test_repeated_pages_do_not_duplicate(store: SqliteTokenStore, repository: SheetsRepository) -> None:
    provider = FakeProvider()
    page = SyncPage(
        added=[plaid_tx("t1", "12.50"), plaid_tx("t1", "12.50")], next_cursor="c1", has_more=False
    )
    provider.pages[""] = [page, page.model_copy(deep=True)]

    link(provider, store, repository)
    store.save_cursor(USER, "item-1", "", NOW)  # as if the cursor had not been saved
    service.sync_all(provider, store, repository, USER, CATEGORIES, "CAD", NOW)

    assert len(repository.list_bank_transactions(BankTransactionFilter())) == 1


def test_cursor_is_not_saved_when_the_sheet_write_fails(
    store: SqliteTokenStore, repository: SheetsRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = FakeProvider()
    link(provider, store, repository)  # cursor c0
    provider.pages["c0"] = [SyncPage(added=[plaid_tx("t1", "1.00")], next_cursor="c1", has_more=False)]

    def fail(*args: object) -> None:
        raise StorageError("Sheets is down")

    monkeypatch.setattr(repository, "apply_bank_changes", fail)
    with pytest.raises(StorageError):
        service.sync_all(provider, store, repository, USER, CATEGORIES, "CAD", NOW)

    assert store.list_items(USER)[0].cursor == "c0"


def test_sync_restarts_when_plaid_reports_a_mid_sync_change(
    store: SqliteTokenStore, repository: SheetsRepository
) -> None:
    provider = FakeProvider()
    link(provider, store, repository)
    provider.pages["c0"] = [SyncPage(added=[plaid_tx("t1", "1.00")], next_cursor="c1", has_more=False)]
    provider.errors = [PlaidError(service.MUTATION_DURING_PAGINATION, "changed")]
    provider.calls.clear()

    result = service.sync_all(provider, store, repository, USER, CATEGORIES, "CAD", NOW)

    assert result.items[0].changes.added == 1
    assert provider.calls == [("sync", "c0"), ("sync", "c0")]


def test_bank_that_needs_a_new_sign_in_is_reported(
    store: SqliteTokenStore, repository: SheetsRepository
) -> None:
    provider = FakeProvider()
    link(provider, store, repository)
    provider.errors = [PlaidError("ITEM_LOGIN_REQUIRED", "sign in again")]

    [item] = service.sync_all(provider, store, repository, USER, CATEGORIES, "CAD", NOW).items

    assert (item.status, item.error) == ("needs_relink", "ITEM_LOGIN_REQUIRED")
    [linked] = service.list_linked(store, repository, USER)
    assert linked.needs_reconnect is True
    # Reconnect signs in again to the same link (update mode); the transactions and cursor stay.
    token = service.create_link_token(provider, store, USER, "item-1")
    assert token == "link-update-access-sandbox-secret"
    service.sync_all(provider, store, repository, USER, CATEGORIES, "CAD", NOW)
    [linked] = service.list_linked(store, repository, USER)
    assert linked.needs_reconnect is False


def test_unlink_revokes_and_keeps_history(store: SqliteTokenStore, repository: SheetsRepository) -> None:
    provider = FakeProvider()
    provider.pages[""] = [SyncPage(added=[plaid_tx("t1", "12.50")], next_cursor="c1", has_more=False)]
    link(provider, store, repository)

    service.unlink_item(provider, store, repository, USER, "item-1")

    assert provider.removed == ["access-sandbox-secret"]
    assert store.list_items(USER) == []
    assert repository.list_bank_accounts() == []
    assert len(repository.list_bank_transactions(BankTransactionFilter())) == 1
    with pytest.raises(service.ItemNotFoundError):
        service.unlink_item(provider, store, repository, USER, "item-1")


# --- API ------------------------------------------------------------------------


@pytest.fixture
def client(
    store: SqliteTokenStore, repository: SheetsRepository
) -> Iterator[tuple[TestClient, FakeProvider]]:
    provider = FakeProvider()
    settings = make_settings(plaid_client_id="id", plaid_secret="secret", token_encryption_key="k" * 43)
    user = SessionData(sub=USER, email="t@example.com", refresh_token="r", spreadsheet_id="s")
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_repository] = lambda: repository
    app.dependency_overrides[get_bank_provider] = lambda: provider
    app.dependency_overrides[get_token_store] = lambda: store
    with TestClient(app) as test_client:
        yield test_client, provider
    app.dependency_overrides.clear()


def test_bank_api_flow(client: tuple[TestClient, FakeProvider], monkeypatch: pytest.MonkeyPatch) -> None:
    http, provider = client
    provider.pages[""] = [SyncPage(added=[plaid_tx("t1", "12.50")], next_cursor="c1", has_more=False)]
    # GET /items reads the store through get_token_store(settings) directly.
    monkeypatch.setattr(
        "app.api.bank.get_token_store", lambda settings: app.dependency_overrides[get_token_store]()
    )

    assert http.post("/api/bank/link-token").json() == {"link_token": f"link-sandbox-{USER}"}
    assert http.post("/api/bank/link-token", json={"item_id": "item-1"}).status_code == 404  # not linked
    linked = http.post("/api/bank/items", json={"public_token": "public-token"})
    assert linked.status_code == 201
    assert linked.json()["changes"]["added"] == 1

    reconnect = http.post("/api/bank/link-token", json={"item_id": "item-1"})
    assert reconnect.json() == {"link_token": "link-update-access-sandbox-secret"}
    status = http.get("/api/bank/items").json()
    assert status["enabled"] is True
    assert status["items"][0]["needs_reconnect"] is False
    assert status["items"][0]["accounts"][0]["mask"] == "3333"
    assert http.post("/api/bank/sync").json()["items"][0]["status"] == "ok"
    [transaction] = http.get("/api/bank/transactions", params={"from": "2026-10-01"}).json()
    assert (transaction["amount"], transaction["category"]) == ("12.50", "Groceries")

    assert http.post("/api/bank/items", json={"public_token": "bad"}).status_code == 400
    assert http.delete("/api/bank/items/item-1").status_code == 204
    assert http.delete("/api/bank/items/item-1").status_code == 404


def test_bank_linking_off_without_plaid_keys() -> None:
    user = SessionData(sub=USER, email="t@example.com", refresh_token="r", spreadsheet_id="s")
    app.dependency_overrides[get_settings] = lambda: make_settings()
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_repository] = lambda: None
    with TestClient(app) as http:
        assert http.get("/api/bank/items").json() == {"enabled": False, "items": []}
        assert http.post("/api/bank/link-token").status_code == 503
    app.dependency_overrides.clear()


def test_bank_api_requires_sign_in() -> None:
    app.dependency_overrides[get_settings] = lambda: make_settings()
    with TestClient(app) as http:
        assert http.get("/api/bank/items").status_code == 401
    app.dependency_overrides.clear()


# --- purchase dates, re-import, refresh (2026-10-16) ------------------------------


def test_the_purchase_date_is_used_not_the_posting_date(
    store: SqliteTokenStore, repository: SheetsRepository
) -> None:
    provider = FakeProvider()
    provider.pages[""] = [
        SyncPage(added=[plaid_tx("t1", "9.99", day=8, authorized_day=3)], next_cursor="c1", has_more=False)
    ]

    link(provider, store, repository)

    [t1] = repository.list_bank_transactions(BankTransactionFilter())
    assert t1.date == date(2026, 10, 3)
    assert store.list_items(USER)[0].bank_updated_at == "2026-10-15T19:01:58Z"


def test_items_imported_the_old_way_are_reimported_once(
    store: SqliteTokenStore, repository: SheetsRepository
) -> None:
    provider = FakeProvider()
    provider.pages[""] = [SyncPage(added=[plaid_tx("t1", "9.99", day=8)], next_cursor="c1", has_more=False)]
    link(provider, store, repository)
    # As if imported before purchase dates: version 1, posting date in the sheet.
    store.save_cursor(USER, "item-1", "c1", NOW, data_version=1)
    provider.pages[""] = [
        SyncPage(added=[plaid_tx("t1", "9.99", day=8, authorized_day=3)], next_cursor="c2", has_more=False)
    ]
    provider.calls.clear()

    service.sync_all(provider, store, repository, USER, CATEGORIES, "CAD", NOW)
    service.sync_all(provider, store, repository, USER, CATEGORIES, "CAD", NOW)

    assert provider.calls == [("sync", ""), ("sync", "c2")]  # from the start once, then incremental
    [t1] = repository.list_bank_transactions(BankTransactionFilter())
    assert t1.date == date(2026, 10, 3)  # corrected in place, not duplicated
    assert store.list_items(USER)[0].data_version == service.DATA_VERSION


def test_sync_button_asks_plaid_to_read_the_bank_at_most_hourly(
    store: SqliteTokenStore, repository: SheetsRepository
) -> None:
    provider = FakeProvider()
    link(provider, store, repository)

    first = service.sync_all(
        provider, store, repository, USER, CATEGORIES, "CAD", "2026-10-16T10:00:00Z", True
    )
    soon = service.sync_all(
        provider, store, repository, USER, CATEGORIES, "CAD", "2026-10-16T10:30:00Z", True
    )
    later = service.sync_all(
        provider, store, repository, USER, CATEGORIES, "CAD", "2026-10-16T11:05:00Z", True
    )
    plain = service.sync_all(provider, store, repository, USER, CATEGORIES, "CAD", "2026-10-16T13:00:00Z")

    assert [r.items[0].refresh_requested for r in (first, soon, later, plain)] == [True, False, True, False]
    assert [c for c in provider.calls if c[0] == "refresh"] == [("refresh", "access-sandbox-secret")] * 2
    assert first.items[0].bank_updated_at == "2026-10-15T19:01:58Z"


def test_token_store_from_before_the_new_columns_is_upgraded(tmp_path: Path) -> None:
    path = tmp_path / "tokens.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """CREATE TABLE plaid_items (item_id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
            institution_id TEXT NOT NULL DEFAULT '', institution_name TEXT NOT NULL DEFAULT '',
            access_token BLOB NOT NULL, cursor TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
            last_synced_at TEXT NOT NULL DEFAULT '');
            INSERT INTO plaid_items (item_id, user_id, institution_name, access_token, cursor, created_at)
            VALUES ('item-1', 'u', 'CIBC', x'00', 'c9', '2026-10-15T00:00:00Z');"""
        )

    SqliteTokenStore(path, key())

    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT institution_name, cursor, data_version, bank_updated_at, refreshed_at FROM plaid_items"
        ).fetchone()
    assert row == ("CIBC", "c9", 1, "", "")  # kept, with defaults for the new columns


def test_unchanged_transactions_are_not_rewritten(
    store: SqliteTokenStore, repository: SheetsRepository
) -> None:
    provider = FakeProvider()
    provider.pages[""] = [SyncPage(added=[plaid_tx("t1", "9.99")], next_cursor="c1", has_more=False)]
    link(provider, store, repository)
    provider.pages["c1"] = [SyncPage(modified=[plaid_tx("t1", "9.99")], next_cursor="c2", has_more=False)]
    api = repository._api  # type: ignore[attr-defined]
    writes_before = api.calls.count("write_values")

    [item] = service.sync_all(provider, store, repository, USER, CATEGORIES, "CAD", NOW).items

    assert item.changes.modified == 0
    assert api.calls.count("write_values") == writes_before


def test_travel_charges_imported_as_transportation_are_moved_once(
    store: SqliteTokenStore, repository: SheetsRepository
) -> None:
    from app.storage.models import MerchantRule

    provider = FakeProvider()
    link(provider, store, repository)
    old_way = [
        plaid_tx("air", "420.00", "TRAVEL_FLIGHTS", name="AIR CANADA 0141234"),
        plaid_tx("hotel", "180.00", "TRAVEL_LODGING", name="MARRIOTT 11"),
        plaid_tx("bus", "12.00", "TRAVEL_OTHER_TRAVEL", name="COMMUTER BUS"),
    ]
    rows = [
        service.to_transaction(t, CATEGORIES, "CAD").model_copy(
            update={"category": "Transportation", "subcategory": ""}
        )
        for t in old_way
    ]
    repository.apply_bank_changes(rows, [])
    repository.save_merchant_rule(
        MerchantRule(merchant="commuter bus", kind="spending", category="Transportation", subcategory="")
    )
    store.save_cursor(USER, "item-1", "c0", NOW, data_version=3)

    service.sync_all(provider, store, repository, USER, CATEGORIES, "CAD", NOW)

    by_id = {
        t.transaction_id: (t.category, t.subcategory)
        for t in repository.list_bank_transactions(BankTransactionFilter())
    }
    assert by_id == {
        "air": ("Travel", "Flights"),
        "hotel": ("Travel", "Lodging"),
        "bus": ("Transportation", ""),  # the user chose Transportation for this merchant
    }
    assert store.list_items(USER)[0].data_version == service.DATA_VERSION


def test_gym_and_sporting_goods_charges_move_to_hobbies_once(
    store: SqliteTokenStore, repository: SheetsRepository
) -> None:
    provider = FakeProvider()
    link(provider, store, repository)
    old = [
        ("gym", "PERSONAL_CARE_GYMS_AND_FITNESS_CENTERS", "Personal Care"),
        ("skis", "GENERAL_MERCHANDISE_SPORTING_GOODS", "Other"),
        ("hair", "PERSONAL_CARE_HAIR_AND_BEAUTY", "Personal Care"),  # not part of the move
    ]
    rows = [
        service.to_transaction(plaid_tx(i, "50.00", code, name=i.upper()), CATEGORIES, "CAD").model_copy(
            update={"category": category, "subcategory": ""}
        )
        for i, code, category in old
    ]
    repository.apply_bank_changes(rows, [])
    store.save_cursor(USER, "item-1", "c0", NOW, data_version=4)

    service.sync_all(provider, store, repository, USER, CATEGORIES, "CAD", NOW)

    by_id = {
        t.transaction_id: (t.category, t.subcategory)
        for t in repository.list_bank_transactions(BankTransactionFilter())
    }
    assert by_id == {
        "gym": ("Hobbies", "Sports & Fitness"),
        "skis": ("Hobbies", "Sports & Fitness"),
        "hair": ("Personal Care", ""),
    }


def test_education_charges_move_from_kids_once(store: SqliteTokenStore, repository: SheetsRepository) -> None:
    provider = FakeProvider()
    link(provider, store, repository)
    old = [
        ("course", "GENERAL_SERVICES_EDUCATION", "Kids", "School Supplies"),
        ("tuition", "GENERAL_SERVICES_EDUCATION", "Kids", ""),  # the user removed the subcategory
        ("daycare", "GENERAL_SERVICES_CHILDCARE", "Kids", "School Supplies"),  # not part of the move
    ]
    rows = [
        service.to_transaction(plaid_tx(i, "50.00", code, name=i.upper()), CATEGORIES, "CAD").model_copy(
            update={"category": category, "subcategory": subcategory}
        )
        for i, code, category, subcategory in old
    ]
    repository.apply_bank_changes(rows, [])
    store.save_cursor(USER, "item-1", "c0", NOW, data_version=8)

    service.sync_all(provider, store, repository, USER, CATEGORIES, "CAD", NOW)

    by_id = {
        t.transaction_id: (t.category, t.subcategory)
        for t in repository.list_bank_transactions(BankTransactionFilter())
    }
    assert by_id == {
        "course": ("Education", "Tuition & Courses"),
        "tuition": ("Kids", ""),
        "daycare": ("Kids", "School Supplies"),
    }


def test_electronics_charges_move_from_other_once(
    store: SqliteTokenStore, repository: SheetsRepository
) -> None:
    provider = FakeProvider()
    link(provider, store, repository)
    old = [
        ("laptop", "GENERAL_MERCHANDISE_ELECTRONICS", "Other"),
        ("tv", "GENERAL_MERCHANDISE_ELECTRONICS", "Entertainment"),  # the user chose another category
        ("gift", "GENERAL_MERCHANDISE_GIFTS_AND_NOVELTIES", "Other"),  # not part of the move
    ]
    rows = [
        service.to_transaction(plaid_tx(i, "50.00", code, name=i.upper()), CATEGORIES, "CAD").model_copy(
            update={"category": category, "subcategory": ""}
        )
        for i, code, category in old
    ]
    repository.apply_bank_changes(rows, [])
    store.save_cursor(USER, "item-1", "c0", NOW, data_version=9)

    service.sync_all(provider, store, repository, USER, CATEGORIES, "CAD", NOW)

    by_id = {
        t.transaction_id: (t.category, t.subcategory)
        for t in repository.list_bank_transactions(BankTransactionFilter())
    }
    assert by_id == {
        "laptop": ("Household", "Electronics & Appliances"),
        "tv": ("Entertainment", ""),
        "gift": ("Other", ""),
    }


def test_update_mode_link_token_sends_the_access_token_without_products(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = PlaidClient("id", "secret", "sandbox", ["CA"])
    bodies: list[dict[str, Any]] = []

    def post(path: str, body: dict[str, Any]) -> dict[str, Any]:
        bodies.append(body)
        return {"link_token": "link-x"}

    monkeypatch.setattr(client, "_post", post)
    client.create_link_token(USER)
    client.create_link_token(USER, "access-old")

    new, update = bodies
    assert new["products"] == ["transactions"] and "access_token" not in new
    assert update["access_token"] == "access-old" and "products" not in update
