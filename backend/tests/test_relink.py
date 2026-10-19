"""Linking a bank again replaces the old link without duplicate transactions (services/relink.py)."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.bank.plaid import PersonalFinanceCategory, PlaidAccount, PlaidTransaction, SyncPage
from app.bank.token_store import SqliteTokenStore
from app.services import bank as service
from app.services.bank_categories import set_category
from app.services.spending_sources import spending_records
from app.sheets.setup import ensure_spreadsheet
from app.storage.models import NO_RECEIPT, BankTransactionFilter, MerchantRule
from app.storage.sheets_repository import SheetsRepository, TabReadCache
from tests.fakes import FakeSheetsApi
from tests.test_bank import CATEGORIES, USER, key

FIRST = "2026-10-16T18:00:00Z"
SECOND = "2026-10-17T19:00:00Z"


def tx(
    transaction_id: str, account_id: str, amount: str, day: int, name: str, pending: bool = False
) -> PlaidTransaction:
    return PlaidTransaction(
        transaction_id=transaction_id,
        account_id=account_id,
        amount=Decimal(amount),
        iso_currency_code="CAD",
        date=date(2026, 10, day),
        name=name,
        pending=pending,
        personal_finance_category=PersonalFinanceCategory(detailed="FOOD_AND_DRINK_GROCERIES"),
    )


class Bank:
    """A fake Plaid where each Link gives a new item, with its own account and transaction IDs."""

    def __init__(self) -> None:
        self.links: list[tuple[str, list[PlaidAccount], list[PlaidTransaction]]] = []
        self.removed: list[str] = []
        self._next = 0

    def add_link(self, mask: str, transactions: list[PlaidTransaction], account_id: str) -> None:
        account = PlaidAccount(
            account_id=account_id, name="Visa", mask=mask, type="credit", subtype="credit card"
        )
        self.links.append((f"access-{len(self.links)}", [account], transactions))

    def exchange_public_token(self, public_token: str) -> tuple[str, str]:
        access_token = self.links[self._next][0]
        self._next += 1
        return access_token, access_token.replace("access", "item")

    def get_institution(self, access_token: str) -> tuple[str, str]:
        return "ins_37", "CIBC"

    def _link(self, access_token: str) -> tuple[str, list[PlaidAccount], list[PlaidTransaction]]:
        return next(link for link in self.links if link[0] == access_token)

    def get_accounts(self, access_token: str) -> list[PlaidAccount]:
        return self._link(access_token)[1]

    def sync_transactions(self, access_token: str, cursor: str) -> SyncPage:
        added = [] if cursor else self._link(access_token)[2]
        return SyncPage(added=added, next_cursor="c1", has_more=False)

    def remove_item(self, access_token: str) -> None:
        self.removed.append(access_token)

    def refresh_transactions(self, access_token: str) -> None:
        pass

    def bank_updated_at(self, access_token: str) -> str:
        return ""

    def create_link_token(self, user_id: str, access_token: str | None = None) -> str:
        return "link"


@pytest.fixture
def store(tmp_path: Path) -> SqliteTokenStore:
    return SqliteTokenStore(tmp_path / "tokens.sqlite3", key())


@pytest.fixture
def repository() -> SheetsRepository:
    api = FakeSheetsApi()
    return SheetsRepository(api, ensure_spreadsheet(api, FIRST), TabReadCache(ttl_seconds=0))


def link(bank: Bank, store: SqliteTokenStore, repository: SheetsRepository, now: str) -> None:
    service.link_item(bank, store, repository, USER, "public", CATEGORIES, "CAD", now)  # type: ignore[arg-type]


def rows(repository: SheetsRepository) -> dict[str, tuple[str, str, str, str]]:
    return {
        t.transaction_id: (t.account_id, t.category, t.receipt_id, t.date.isoformat())
        for t in repository.list_bank_transactions(BankTransactionFilter())
    }


def test_linking_again_replaces_the_old_link(store: SqliteTokenStore, repository: SheetsRepository) -> None:
    bank = Bank()
    bank.add_link(
        "0639",
        [
            tx("old-a", "acc-old", "17.16", 15, "SUZUYA JAPANESE MARKET BURNABY, BC"),
            tx("old-b", "acc-old", "4.50", 12, "TIM HORTONS #123"),
            tx("old-c", "acc-old", "9.99", 3, "OLD HISTORY STORE"),  # the new link doesn't return it
            tx("old-p", "acc-old", "30.00", 16, "PENDING GAS", pending=True),
        ],
        account_id="acc-old",
    )
    bank.add_link(
        "0639",
        [
            tx("new-a", "acc-new", "17.16", 15, "SUZUYA JAPANESE MARKET BURNABY, BC"),
            tx("new-b", "acc-new", "4.50", 13, "TIM HORTONS #123"),  # posted a day later
            tx("new-d", "acc-new", "8.00", 17, "NEW CHARGE"),
        ],
        account_id="acc-new",
    )
    link(bank, store, repository, FIRST)
    set_category(repository, "old-a", "spending", "Dining Out", "", apply_to_merchant=False)
    repository.set_bank_receipt_ids({"old-b": NO_RECEIPT})

    link(bank, store, repository, SECOND)

    assert rows(repository) == {
        "new-a": ("acc-new", "Dining Out", "", "2026-10-15"),  # the user's category came along
        "new-b": ("acc-new", "Groceries", NO_RECEIPT, "2026-10-13"),
        "new-d": ("acc-new", "Groceries", "", "2026-10-17"),
        "old-c": ("acc-new", "Groceries", "", "2026-10-03"),  # older history moved to the new account
    }  # old-p: a pending charge of the old link never posts, so it is dropped
    assert [item.item_id for item in store.list_items(USER)] == ["item-1"]
    assert bank.removed == ["access-0"]
    assert [a.account_id for a in repository.list_bank_accounts()] == ["acc-new"]
    total = sum((r.total for r in spending_records(repository)), Decimal(0))
    assert total == Decimal("39.65")  # each purchase counted once


def test_linking_again_after_unlinking_merges_the_kept_history(
    store: SqliteTokenStore, repository: SheetsRepository
) -> None:
    bank = Bank()
    bank.add_link("0639", [tx("old-a", "acc-old", "17.16", 15, "SUZUYA")], account_id="acc-old")
    bank.add_link("0639", [tx("new-a", "acc-new", "17.16", 15, "SUZUYA")], account_id="acc-new")
    link(bank, store, repository, FIRST)
    service.unlink_item(bank, store, repository, USER, "item-0")  # type: ignore[arg-type]

    link(bank, store, repository, SECOND)

    assert rows(repository) == {"new-a": ("acc-new", "Groceries", "", "2026-10-15")}
    assert bank.removed == ["access-0"]  # only the unlink itself


def test_other_accounts_at_the_same_bank_are_kept(
    store: SqliteTokenStore, repository: SheetsRepository
) -> None:
    bank = Bank()
    bank.add_link("0639", [tx("mine", "acc-mine", "17.16", 15, "SUZUYA")], account_id="acc-mine")
    bank.add_link("7777", [tx("theirs", "acc-theirs", "17.16", 15, "SUZUYA")], account_id="acc-theirs")
    link(bank, store, repository, FIRST)

    link(bank, store, repository, SECOND)  # a partner's login at the same bank

    assert set(rows(repository)) == {"mine", "theirs"}
    assert len(store.list_items(USER)) == 2
    assert bank.removed == []


def test_sync_cleans_up_a_bank_linked_twice(store: SqliteTokenStore, repository: SheetsRepository) -> None:
    bank = Bank()
    bank.add_link("0639", [tx("old-a", "acc-old", "17.16", 15, "SUZUYA")], account_id="acc-old")
    bank.add_link("0639", [tx("new-a", "acc-new", "17.16", 15, "SUZUYA")], account_id="acc-new")
    # Both links imported before this fix: every charge is in the sheet twice.
    for now in (FIRST, SECOND):
        access_token, item_id = bank.exchange_public_token("public")
        store.add_item(USER, item_id, access_token, "ins_37", "CIBC", now)
        item = next(i for i in store.list_items(USER) if i.item_id == item_id)
        service._sync_item(bank, store, repository, item, CATEGORIES, "CAD", now)  # type: ignore[arg-type]
    assert len(rows(repository)) == 2

    result = service.sync_all(bank, store, repository, USER, CATEGORIES, "CAD", SECOND)  # type: ignore[arg-type]

    assert rows(repository) == {"new-a": ("acc-new", "Groceries", "", "2026-10-15")}
    assert [r.item_id for r in result.items] == ["item-1"]


def test_choices_come_back_after_unlinking_with_removal_and_linking_again(
    store: SqliteTokenStore, repository: SheetsRepository
) -> None:
    bank = Bank()
    bank.add_link(
        "0639",
        [
            tx("old-a", "acc-old", "21.40", 15, "RAMEN ARASHI VANCOUVER, BC"),
            tx("old-b", "acc-old", "4.50", 12, "TIM HORTONS #123"),
            tx("old-c", "acc-old", "9.00", 11, "SAFEWAY #4"),
        ],
        account_id="acc-old",
    )
    bank.add_link(
        "0639",
        [
            tx("new-a", "acc-new", "21.40", 15, "RAMEN ARASHI VANCOUVER, BC"),
            tx("new-b", "acc-new", "4.50", 12, "TIM HORTONS #123"),
            tx("new-c", "acc-new", "9.00", 11, "SAFEWAY #4"),
        ],
        account_id="acc-new",
    )
    link(bank, store, repository, FIRST)
    set_category(repository, "old-a", "spending", "Dining Out", "", apply_to_merchant=False)  # one-off
    repository.set_bank_receipt_ids({"old-b": NO_RECEIPT})
    # Unlink with "also remove its transactions", then link the bank again.
    service.unlink_item(bank, store, repository, USER, "item-0", remove_transactions=True)  # type: ignore[arg-type]

    link(bank, store, repository, SECOND)

    assert rows(repository) == {
        "new-a": ("acc-new", "Dining Out", "", "2026-10-15"),
        "new-b": ("acc-new", "Groceries", NO_RECEIPT, "2026-10-12"),
        "new-c": ("acc-new", "Groceries", "", "2026-10-11"),  # nothing changed, nothing carried
    }


def test_one_time_recovery_for_rows_imported_before(
    store: SqliteTokenStore, repository: SheetsRepository
) -> None:
    bank = Bank()
    bank.add_link(
        "0639",
        [
            tx("old-a", "acc-old", "21.40", 15, "RAMEN ARASHI VANCOUVER, BC"),
            tx("old-c", "acc-old", "9.00", 11, "SAFEWAY #4"),
        ],
        account_id="acc-old",
    )
    bank.add_link(
        "0639",
        [
            tx("new-a", "acc-new", "21.40", 15, "RAMEN ARASHI VANCOUVER, BC"),
            tx("new-c", "acc-new", "9.00", 11, "SAFEWAY #4"),
        ],
        account_id="acc-new",
    )
    link(bank, store, repository, FIRST)
    set_category(repository, "old-a", "spending", "Dining Out", "", apply_to_merchant=False)
    set_category(repository, "old-c", "spending", "Household", "", apply_to_merchant=False)
    service.unlink_item(bank, store, repository, USER, "item-0", remove_transactions=True)  # type: ignore[arg-type]
    # The bank was linked again before this fix: its rows came in with Plaid's categories.
    access_token, item_id = bank.exchange_public_token("public")
    store.add_item(USER, item_id, access_token, "ins_37", "CIBC", SECOND)
    item = next(i for i in store.list_items(USER) if i.item_id == item_id)
    service._sync_item(bank, store, repository, item, CATEGORIES, "CAD", SECOND)  # type: ignore[arg-type]
    store.save_cursor(USER, item_id, "c1", SECOND, data_version=10)
    set_category(
        repository, "new-c", "spending", "Transportation", "", apply_to_merchant=False
    )  # changed since

    service.sync_all(bank, store, repository, USER, CATEGORIES, "CAD", SECOND)  # type: ignore[arg-type]

    categories = {i: c for i, (_, c, _, _) in rows(repository).items()}
    assert categories == {"new-a": "Dining Out", "new-c": "Transportation"}
    assert store.list_items(USER)[0].data_version == service.DATA_VERSION


def test_a_remembered_category_under_an_old_name_comes_back(
    store: SqliteTokenStore, repository: SheetsRepository
) -> None:
    bank = Bank()
    bank.add_link(
        "0639", [tx("old-s", "acc-old", "89.99", 14, "SKECHERS #123 BURNABY, BC")], account_id="acc-old"
    )
    bank.add_link(
        "0639", [tx("new-s", "acc-new", "89.99", 14, "SKECHERS #123 BURNABY, BC")], account_id="acc-new"
    )
    link(bank, store, repository, FIRST)
    # Chosen for "all charges" while the charge showed Plaid's name, which the app no longer shows.
    repository.save_merchant_rule(
        MerchantRule(merchant="skechers usa", kind="spending", category="Hobbies", subcategory="")
    )
    repository.update_bank_fields({"old-s": {"merchant": "Skechers USA", "category": "Hobbies"}})
    service.unlink_item(bank, store, repository, USER, "item-0", remove_transactions=True)  # type: ignore[arg-type]

    link(bank, store, repository, SECOND)

    assert rows(repository)["new-s"][1] == "Hobbies"


def test_remembered_categories_are_found_by_the_bank_text_once_for_existing_rows(
    store: SqliteTokenStore, repository: SheetsRepository
) -> None:
    bank = Bank()
    bank.add_link("0639", [tx("s", "acc", "89.99", 14, "SKECHERS #123 BURNABY, BC")], account_id="acc")
    link(bank, store, repository, FIRST)
    # Saved under the bank text's key while the row shows another name.
    repository.update_bank_fields({"s": {"merchant": "Sketchers Inc"}})
    repository.save_merchant_rule(
        MerchantRule(merchant="skechers burnaby", kind="spending", category="Hobbies", subcategory="")
    )
    store.save_cursor(USER, "item-0", "c1", FIRST, data_version=11)

    service.sync_all(bank, store, repository, USER, CATEGORIES, "CAD", SECOND)  # type: ignore[arg-type]

    assert rows(repository)["s"][1] == "Hobbies"


def test_a_stale_other_is_not_a_choice_and_the_newest_link_wins(
    store: SqliteTokenStore, repository: SheetsRepository
) -> None:
    bank = Bank()
    for n in range(3):
        bank.add_link(
            "0639", [tx(f"kfc-{n}", f"acc-{n}", "12.49", 3, "KFC GUILDFORD")], account_id=f"acc-{n}"
        )
    bank.add_link("0639", [tx("tim-3", "acc-3", "4.50", 5, "TIM HORTONS #1")], account_id="acc-3")
    link(bank, store, repository, FIRST)
    # Imported while Plaid's category was vague; Plaid improved it later, the row kept "Other".
    repository.update_bank_fields({"kfc-0": {"category": "Other"}})
    service.unlink_item(bank, store, repository, USER, "item-0", remove_transactions=True)  # type: ignore[arg-type]
    link(bank, store, repository, "2026-10-17T00:00:00Z")
    assert rows(repository)["kfc-1"][1] == "Groceries"  # the stale "Other" didn't come along
    set_category(repository, "kfc-1", "spending", "Dining Out", "", apply_to_merchant=False)
    service.unlink_item(bank, store, repository, USER, "item-1", remove_transactions=True)  # type: ignore[arg-type]

    link(bank, store, repository, SECOND)

    [kfc] = [
        t for t in repository.list_bank_transactions(BankTransactionFilter()) if t.transaction_id == "kfc-2"
    ]
    assert (kfc.category, kfc.edited) == ("Dining Out", True)  # the user's choice from the newest link
