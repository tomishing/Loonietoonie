from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.auth.dependencies import get_current_user
from app.auth.session import SessionData
from app.bank.plaid import PlaidTransaction
from app.config import get_settings
from app.main import app
from app.services import bank
from app.services.matching import candidates, find_matches, names_match, refresh_matches, set_match
from app.services.names import bank_key
from app.services.spending_sources import spending_records
from app.services.summary import summarize
from app.sheets.setup import ensure_spreadsheet
from app.storage.dependencies import get_repository
from app.storage.models import (
    NO_RECEIPT,
    BankTransaction,
    BankTransactionFilter,
    ItemDraft,
    Receipt,
    ReceiptDraft,
    ReceiptFilter,
)
from app.storage.sheets_repository import SheetsRepository, TabReadCache
from tests.fakes import FakeSheetsApi
from tests.test_auth import make_settings
from tests.test_summary import receipt

D = Decimal


def charge(
    transaction_id: str,
    amount: str,
    day: str,
    merchant: str = "",
    description: str = "",
    kind: str = "spending",
    category: str = "Groceries",
    receipt_id: str = "",
    currency: str = "CAD",
) -> BankTransaction:
    return BankTransaction(
        transaction_id=transaction_id,
        account_id="acc-1",
        date=date.fromisoformat(day),
        merchant=merchant,
        description=description,
        amount=D(amount),
        currency=currency,
        kind=kind,  # type: ignore[arg-type]
        category=category,
        subcategory="",
        plaid_category="",
        pending=False,
        receipt_id=receipt_id,
    )


# --- names and rules --------------------------------------------------------------


@pytest.mark.parametrize(
    ("store", "merchant", "description", "expected"),
    [
        ("FreshCo", "", "FRESHCO #1234 TORONTO ON", True),
        ("Tim Hortons", "Tim Hortons", "", True),
        ("Café Olimpico", "", "CAFE OLIMPICO MONTREAL", True),  # accents ignored
        ("FreshCo", "Metro", "METRO 123", False),
        ("セブン-イレブン", "7-Eleven", "", False),  # different scripts never match by name
        ("The Store", "", "STORE 99", False),  # noise words only
    ],
)
def test_names_match(store: str, merchant: str, description: str, expected: bool) -> None:
    assert names_match(store, charge("t", "1", "2026-10-05", merchant, description)) is expected


def test_exact_amount_and_name_within_the_posting_window() -> None:
    r = receipt("2026-10-05", "FreshCo", [("Milk", "5.49", "Groceries")])
    charges = [
        charge("early", "5.49", "2026-10-03", "FreshCo"),  # 2 days before: too early
        charge("posted", "5.49", "2026-10-07", "FreshCo"),
    ]

    assert find_matches([r], charges) == {"posted": r.receipt_id}


def test_restaurant_tip_on_the_card_matches_by_name() -> None:
    r = receipt(
        "2026-10-05", "Ramen Isshin", [("Ramen", "18.00", "Dining Out"), ("HST", "2.34", "Tax & Fees")]
    )

    assert find_matches([r], [charge("tip", "24.00", "2026-10-06", "Ramen Isshin")]) == {"tip": r.receipt_id}
    # The same 18% extra at a grocery store is not a tip.
    groceries = receipt("2026-10-05", "FreshCo", [("Milk", "20.34", "Groceries")])
    assert find_matches([groceries], [charge("more", "24.00", "2026-10-06", "FreshCo")]) == {}


def test_amount_only_match_needs_to_be_the_only_option() -> None:
    a = receipt("2026-10-05", "セブン-イレブン", [("おにぎり", "4.00", "Groceries")])
    lone = charge("lone", "4.00", "2026-10-05", "7-Eleven")
    assert find_matches([a], [lone]) == {"lone": a.receipt_id}

    b = receipt("2026-10-05", "Corner Shop", [("Gum", "4.00", "Groceries")])
    assert find_matches([a, b], [lone]) == {}  # two receipts of 4.00, no name to tell them apart


def test_one_receipt_per_charge_and_existing_choices_are_respected() -> None:
    r = receipt("2026-10-05", "FreshCo", [("Milk", "5.49", "Groceries")])
    charges = [
        charge("dup1", "5.49", "2026-10-05", "FreshCo"),
        charge("dup2", "5.49", "2026-10-06", "FreshCo"),
        charge("no", "5.49", "2026-10-05", "FreshCo", receipt_id=NO_RECEIPT),
        charge("transfer", "5.49", "2026-10-05", "FreshCo", kind="transfer"),
        charge("usd", "5.49", "2026-10-05", "FreshCo", currency="USD"),
    ]

    assert find_matches([r], charges) == {"dup1": r.receipt_id}  # same day scores higher


# --- with storage ---------------------------------------------------------------


@pytest.fixture
def repository() -> SheetsRepository:
    api = FakeSheetsApi()
    return SheetsRepository(api, ensure_spreadsheet(api, "2026-10-15T00:00:00Z"), TabReadCache(ttl_seconds=0))


def save(repository: SheetsRepository, store: str, day: str, items: list[tuple[str, str, str]]) -> Receipt:
    drafts = [ItemDraft(name=name, price=D(price), category=category) for name, price, category in items]
    total = sum((d.price for d in drafts), D(0))
    return repository.create_receipt(
        ReceiptDraft(
            store=store, purchased_at=date.fromisoformat(day), total=total, currency="CAD", items=drafts
        )
    )


def test_matched_charge_counts_once_with_the_receipts_items(repository: SheetsRepository) -> None:
    r = save(
        repository, "FreshCo", "2026-10-05", [("Milk", "5.49", "Groceries"), ("Soap", "3.00", "Household")]
    )
    repository.apply_bank_changes(
        [
            charge("matched", "8.49", "2026-10-06", "FreshCo", category="Groceries"),
            charge("uber", "18.90", "2026-10-07", "Uber", category="Transportation"),
            charge("payment", "-250", "2026-10-08", "Card payment", kind="transfer", category=""),
        ],
        [],
    )

    assert refresh_matches(repository) == 1
    records = spending_records(repository)
    assert sorted(x.store for x in records) == ["FreshCo", "Uber"]
    summary = summarize(records, "2026-10", "CAD", "CAD", date(2026, 10, 15))
    assert summary.total == D("27.39")
    assert {c.category: c.total for c in summary.categories} == {
        "Groceries": D("5.49"),
        "Household": D("3.00"),
        "Transportation": D("18.90"),
    }
    assert summary.bank_transaction_count == 1
    assert [s.store for s in summary.top_stores] == ["Uber", "FreshCo"]
    assert [i.name for i in summary.top_items] == ["Milk", "Soap"]  # charges aren't "items bought"

    # Deleting the receipt brings the charge back on its own.
    repository.delete_receipt(r.receipt_id)
    refresh_matches(repository)
    assert sorted(x.store for x in spending_records(repository)) == ["FreshCo", "Uber"]
    assert all(x.receipt_id.startswith("bank:") for x in spending_records(repository))


def test_receipt_counts_at_what_the_card_was_charged(repository: SheetsRepository) -> None:
    dinner = save(
        repository,
        "Sushi Well",
        "2026-10-30",
        [("Ramen", "30.00", "Dining Out"), ("GST", "1.50", "Tax & Fees")],
    )
    shop = save(repository, "FreshCo", "2026-10-05", [("Milk", "5.49", "Groceries")])
    repository.apply_bank_changes(
        [
            # The tip is on the card only, and the charge posts in the next month.
            charge("dinner", "36.23", "2026-11-02", "Sushi Well", category="Dining Out"),
            charge("shop", "5.49", "2026-10-05", "FreshCo"),
        ],
        [],
    )
    set_match(repository, "dinner", dinner.receipt_id)
    set_match(repository, "shop", shop.receipt_id)

    october = ReceiptFilter(date_from=date(2026, 10, 1), date_to=date(2026, 10, 31))
    records = {r.store: r for r in spending_records(repository, october)}
    assert records["Sushi Well"].total == D("36.23")
    assert records["Sushi Well"].items[-1].price == D("4.73")
    assert records["Sushi Well"].items[-1].category == "Dining Out"  # not Tax & Fees
    assert records["FreshCo"].total == D("5.49")
    assert len(records["FreshCo"].items) == 1

    summary = summarize(list(records.values()), "2026-10", "CAD", "CAD", date(2026, 10, 31))
    assert summary.total == D("41.72")
    assert {c.category: c.total for c in summary.categories}["Dining Out"] == D("34.73")
    assert [i.name for i in summary.top_items] == ["Ramen", "Milk"]  # the tip isn't an item bought

    # With a category filter, the difference counts only when its category is included.
    taxes = spending_records(repository, ReceiptFilter(category="Tax & Fees"))
    assert [(r.store, r.total) for r in taxes] == [("Sushi Well", D("31.50"))]


def test_category_filter_applies_to_charges(repository: SheetsRepository) -> None:
    repository.apply_bank_changes(
        [
            charge("uber", "18.90", "2026-10-07", "Uber", category="Transportation"),
            charge("coffee", "4.00", "2026-10-07", "Starbucks", category="Dining Out"),
        ],
        [],
    )

    assert [x.store for x in spending_records(repository, ReceiptFilter(category="dining out"))] == [
        "Starbucks"
    ]


def test_manual_choices(repository: SheetsRepository) -> None:
    r = save(repository, "Corner Shop", "2026-10-05", [("Gum", "4.00", "Groceries")])
    repository.apply_bank_changes(
        [charge("a", "4.00", "2026-10-05", "SQ *CORNER"), charge("b", "4.25", "2026-10-06", "Corner Shop")],
        [],
    )
    refresh_matches(repository)  # "a" matches: the only 4.00 option, and "corner" is in both names

    found = candidates(repository, "b")
    assert [c.receipt.receipt_id for c in found] == [r.receipt_id]

    set_match(repository, "b", r.receipt_id)  # moves the receipt from "a" to "b"
    by_id = {
        t.transaction_id: t.receipt_id for t in repository.list_bank_transactions(BankTransactionFilter())
    }
    assert by_id == {"a": "", "b": r.receipt_id}

    set_match(repository, "b", None)
    refresh_matches(repository)  # "b" stays unmatched; "a" gets the receipt again
    by_id = {
        t.transaction_id: t.receipt_id for t in repository.list_bank_transactions(BankTransactionFilter())
    }
    assert by_id == {"a": r.receipt_id, "b": NO_RECEIPT}


def test_saving_a_receipt_matches_an_imported_charge(repository: SheetsRepository) -> None:
    repository.apply_bank_changes([charge("c", "12.00", "2026-10-06", "Pho Hung", category="Dining Out")], [])
    user = SessionData(sub="u", email="t@example.com", refresh_token="r", spreadsheet_id="s")
    app.dependency_overrides[get_settings] = lambda: make_settings()
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_repository] = lambda: repository
    with TestClient(app) as http:
        body = {
            "store": "Pho Hung",
            "purchased_at": "2026-10-05",
            "total": "12.00",
            "currency": "CAD",
            "items": [{"name": "Pho", "price": "12.00", "category": "Dining Out"}],
        }
        created = http.post("/api/receipts", json=body).json()
        [match] = http.get("/api/bank/transactions/c/candidates").json()
        assert (match["receipt_id"], match["likely"]) == (created["receipt_id"], True)
        assert http.put("/api/bank/transactions/c/receipt", json={"receipt_id": None}).status_code == 204
        assert http.put("/api/bank/transactions/zzz/receipt", json={"receipt_id": None}).status_code == 404
        summary = http.get("/api/summary", params={"month": "2026-10"}).json()
    app.dependency_overrides.clear()

    [transaction] = repository.list_bank_transactions(BankTransactionFilter())
    assert transaction.receipt_id == NO_RECEIPT
    # Marked "no receipt": the charge counts on its own, next to the receipt.
    assert summary["total"] == "24.00"
    assert summary["bank_transaction_count"] == 1


def test_unlink_can_remove_imported_transactions(repository: SheetsRepository) -> None:
    from app.services import bank
    from app.storage.models import BankAccount

    repository.save_bank_accounts(
        [
            BankAccount(
                account_id="acc-1",
                item_id="item-1",
                institution="B",
                name="Card",
                mask="1",
                type="credit",
                subtype="",
                currency="CAD",
            )
        ]
    )
    repository.apply_bank_changes([charge("x", "1.00", "2026-10-06", "Shop")], [])

    class Store:
        def list_items(self, user_id: str) -> list[object]:
            from app.bank.token_store import LinkedItem

            return [LinkedItem("item-1", user_id, "", "B", "tok", "", "", "")]

        def delete_item(self, user_id: str, item_id: str) -> bool:
            return True

    class Provider:
        def remove_item(self, access_token: str) -> None:
            pass

    removed = bank.unlink_item(Provider(), Store(), repository, "u", "item-1", remove_transactions=True)  # type: ignore[arg-type]

    assert removed == 1
    assert repository.list_bank_transactions(BankTransactionFilter()) == []


# --- names learned from receipts ----------------------------------------------------


def test_matched_receipt_names_the_merchant_on_all_its_charges(repository: SheetsRepository) -> None:
    repository.apply_bank_changes(
        [
            charge("a", "18.40", "2026-10-06", "Triple Kingsway", "TRIPLE O'S KINGSWAY BURNABY, BC"),
            charge("b", "9.10", "2026-09-20", "Triple Kingsway", "TRIPLE O'S KINGSWAY BURNABY, BC"),
            charge("c", "4.00", "2026-10-06", "Starbucks", "STARBUCKS #1"),
        ],
        [],
    )
    save(repository, "Triple O's Kingsway", "2026-10-05", [("Burger", "18.40", "Dining Out")])

    refresh_matches(repository)

    by_id = {t.transaction_id: t for t in repository.list_bank_transactions(BankTransactionFilter())}
    assert [by_id[i].merchant for i in "abc"] == ["Triple O's Kingsway", "Triple O's Kingsway", "Starbucks"]
    assert repository.list_merchant_names() == {
        bank_key("TRIPLE O'S KINGSWAY BURNABY, BC"): "Triple O's Kingsway"
    }
    # A new charge from the same place takes the learned name on import.
    categories = repository.list_categories()
    new = bank.to_transaction(
        PlaidTransaction(
            transaction_id="d",
            account_id="acc",
            amount=D("7.00"),
            iso_currency_code="CAD",
            date=date(2026, 10, 9),
            name="TRIPLE O'S KINGSWAY BURNABY, BC",
            merchant_name="Triple O",
        ),
        categories,
        "CAD",
        names=repository.list_merchant_names(),
    )
    assert new.merchant == "Triple O's Kingsway"


def test_amount_only_match_teaches_no_name(repository: SheetsRepository) -> None:
    repository.apply_bank_changes([charge("a", "12.00", "2026-10-06", "Seven", "7-ELEVEN #3341")], [])
    r = save(repository, "セブン-イレブン", "2026-10-05", [("Onigiri", "12.00", "Groceries")])

    refresh_matches(repository)

    [transaction] = repository.list_bank_transactions(BankTransactionFilter())
    assert transaction.receipt_id == r.receipt_id  # the only option, so it matches
    assert transaction.merchant == "Seven"  # but the name is not learned
    assert repository.list_merchant_names() == {}


def test_a_newer_receipt_replaces_the_learned_name(repository: SheetsRepository) -> None:
    text = "SAVE ON FOODS #123 VANCOUVER"
    repository.apply_bank_changes([charge("a", "10.00", "2026-10-02", "Save On", text)], [])
    save(repository, "Save On Foods", "2026-10-02", [("Milk", "10.00", "Groceries")])
    refresh_matches(repository)
    repository.apply_bank_changes([charge("b", "20.00", "2026-10-09", "Save On", text)], [])
    save(repository, "Save-On-Foods Vancouver", "2026-10-09", [("Eggs", "20.00", "Groceries")])

    refresh_matches(repository)

    assert repository.list_merchant_names() == {bank_key(text): "Save-On-Foods Vancouver"}
    merchants = {t.merchant for t in repository.list_bank_transactions(BankTransactionFilter())}
    assert merchants == {"Save-On-Foods Vancouver"}
