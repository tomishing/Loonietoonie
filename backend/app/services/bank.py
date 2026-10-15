"""Bank and card linking (phase 10a): link through Plaid, import transactions into the Sheet.

Sync runs on demand (when the app asks), never in the background, so the server only needs the
user's session, not a stored Google token. The sync cursor is saved only after the Sheet write
succeeds; a failed sync is simply repeated, and transaction IDs prevent duplicates.
"""

import threading
from collections import defaultdict
from typing import Literal

from pydantic import BaseModel

from app.bank.categories import map_category
from app.bank.plaid import BankProvider, PlaidError, PlaidTransaction, SyncPage
from app.bank.token_store import LinkedItem, TokenStore
from app.services.matching import refresh_matches
from app.storage.models import BankAccount, BankChanges, BankTransaction, Category
from app.storage.repository import Repository

# Plaid asks to restart the whole sync from the first cursor when data changes mid-way.
MUTATION_DURING_PAGINATION = "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION"
MAX_SYNC_ATTEMPTS = 3
# The user has to sign in to the bank again through Plaid Link (update mode, later).
RELINK_CODES = frozenset({"ITEM_LOGIN_REQUIRED", "PENDING_EXPIRATION", "PENDING_DISCONNECT"})

ItemStatus = Literal["ok", "needs_relink", "error"]

_user_locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)


class ItemNotFoundError(Exception):
    pass


class LinkedInstitution(BaseModel):
    item_id: str
    institution: str
    last_synced_at: str
    accounts: list[BankAccount]


class ItemSyncResult(BaseModel):
    item_id: str
    institution: str
    status: ItemStatus
    changes: BankChanges = BankChanges()
    error: str | None = None


class SyncResult(BaseModel):
    items: list[ItemSyncResult]


def to_transaction(
    plaid: PlaidTransaction, categories: list[Category], default_currency: str
) -> BankTransaction:
    category = plaid.personal_finance_category
    primary, detailed = (category.primary, category.detailed) if category else ("", "")
    mapped = map_category(primary, detailed, categories)
    currency = (plaid.iso_currency_code or plaid.unofficial_currency_code or default_currency).upper()
    return BankTransaction(
        transaction_id=plaid.transaction_id,
        account_id=plaid.account_id,
        date=plaid.date,
        merchant=(plaid.merchant_name or "").strip(),
        description=plaid.name.strip(),
        amount=plaid.amount,
        currency=currency,
        kind=mapped.kind,
        category=mapped.category,
        subcategory=mapped.subcategory,
        plaid_category=detailed or primary,
        pending=plaid.pending,
    )


def to_accounts(provider: BankProvider, item: LinkedItem, default_currency: str) -> list[BankAccount]:
    return [
        BankAccount(
            account_id=account.account_id,
            item_id=item.item_id,
            institution=item.institution_name,
            name=account.name or account.official_name or "",
            mask=account.mask or "",
            type=account.type,
            subtype=account.subtype or "",
            currency=(
                account.iso_currency_code or account.unofficial_currency_code or default_currency
            ).upper(),
        )
        for account in provider.get_accounts(item.access_token)
    ]


def create_link_token(provider: BankProvider, user_id: str) -> str:
    return provider.create_link_token(user_id)


def link_item(
    provider: BankProvider,
    store: TokenStore,
    repository: Repository,
    user_id: str,
    public_token: str,
    categories: list[Category],
    default_currency: str,
    now: str,
) -> ItemSyncResult:
    """Finish a Plaid Link: store the encrypted token, save the accounts, import transactions."""
    access_token, item_id = provider.exchange_public_token(public_token)
    institution_id, institution_name = provider.get_institution(access_token)
    store.add_item(user_id, item_id, access_token, institution_id, institution_name, now)
    item = next(i for i in store.list_items(user_id) if i.item_id == item_id)
    result = _sync_item(provider, store, repository, item, categories, default_currency, now)
    refresh_matches(repository)
    return result


def list_linked(store: TokenStore, repository: Repository, user_id: str) -> list[LinkedInstitution]:
    accounts = repository.list_bank_accounts()
    return [
        LinkedInstitution(
            item_id=item.item_id,
            institution=item.institution_name,
            last_synced_at=item.last_synced_at,
            accounts=[account for account in accounts if account.item_id == item.item_id],
        )
        for item in store.list_items(user_id)
    ]


def unlink_item(
    provider: BankProvider,
    store: TokenStore,
    repository: Repository,
    user_id: str,
    item_id: str,
    remove_transactions: bool = False,
) -> int:
    """Revoke access at Plaid and forget the token. Imported transactions stay in the Sheet unless
    `remove_transactions` (then they are soft-deleted). Returns the number of removed transactions."""
    item = next((i for i in store.list_items(user_id) if i.item_id == item_id), None)
    if item is None:
        raise ItemNotFoundError(item_id)
    try:
        provider.remove_item(item.access_token)
    except PlaidError as exc:
        if exc.code not in {"ITEM_NOT_FOUND", "INVALID_ACCESS_TOKEN"}:
            raise
    account_ids = [a.account_id for a in repository.list_bank_accounts() if a.item_id == item_id]
    store.delete_item(user_id, item_id)
    repository.remove_bank_accounts(item_id)
    return repository.remove_bank_transactions(account_ids) if remove_transactions else 0


def sync_all(
    provider: BankProvider,
    store: TokenStore,
    repository: Repository,
    user_id: str,
    categories: list[Category],
    default_currency: str,
    now: str,
) -> SyncResult:
    """Import new, changed, and removed transactions for each of the user's linked items."""
    with _user_locks[user_id]:  # two syncs at once could append the same new rows twice
        results = [
            _sync_item(provider, store, repository, item, categories, default_currency, now)
            for item in store.list_items(user_id)
        ]
        refresh_matches(repository)  # new charges may match receipts already saved
        return SyncResult(items=results)


def _sync_item(
    provider: BankProvider,
    store: TokenStore,
    repository: Repository,
    item: LinkedItem,
    categories: list[Category],
    default_currency: str,
    now: str,
) -> ItemSyncResult:
    try:
        repository.save_bank_accounts(to_accounts(provider, item, default_currency))
        pages = _collect_pages(provider, item)
        upserts = [
            to_transaction(t, categories, default_currency)
            for page in pages
            for t in [*page.added, *page.modified]
        ]
        removed = [transaction_id for page in pages for transaction_id in page.removed]
        changes = repository.apply_bank_changes(upserts, removed)
    except PlaidError as exc:
        status: ItemStatus = "needs_relink" if exc.code in RELINK_CODES else "error"
        return ItemSyncResult(
            item_id=item.item_id, institution=item.institution_name, status=status, error=exc.code
        )
    store.save_cursor(item.user_id, item.item_id, pages[-1].next_cursor, now)
    return ItemSyncResult(
        item_id=item.item_id, institution=item.institution_name, status="ok", changes=changes
    )


def _collect_pages(provider: BankProvider, item: LinkedItem) -> list[SyncPage]:
    """All pages since the saved cursor. Restarts from it if Plaid reports a mid-sync change."""
    for attempt in range(MAX_SYNC_ATTEMPTS):
        pages: list[SyncPage] = []
        cursor = item.cursor
        try:
            while True:
                page = provider.sync_transactions(item.access_token, cursor)
                pages.append(page)
                cursor = page.next_cursor
                if not page.has_more:
                    return pages
        except PlaidError as exc:
            if exc.code != MUTATION_DURING_PAGINATION or attempt == MAX_SYNC_ATTEMPTS - 1:
                raise
    raise AssertionError("unreachable")
