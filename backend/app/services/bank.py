"""Bank and card linking (phase 10a): link through Plaid, import transactions into the Sheet.

Sync runs on demand (when the app asks), never in the background, so the server only needs the
user's session, not a stored Google token. The sync cursor is saved only after the Sheet write
succeeds; a failed sync is simply repeated, and transaction IDs prevent duplicates.
"""

import logging
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel

from app.bank.categories import OTHER, map_category
from app.bank.plaid import BankProvider, PlaidError, PlaidTransaction, SyncPage
from app.bank.token_store import LinkedItem, TokenStore
from app.services.locks import user_lock
from app.services.matching import refresh_matches
from app.services.names import bank_key, display_name, merchant_key
from app.services.relink import merge_replaced_accounts
from app.storage.models import (
    BankAccount,
    BankChanges,
    BankTransaction,
    BankTransactionFilter,
    Category,
    MerchantRule,
)
from app.storage.repository import Repository

# Plaid asks to restart the whole sync from the first cursor when data changes mid-way.
MUTATION_DURING_PAGINATION = "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION"
MAX_SYNC_ATTEMPTS = 3
# The user has to sign in to the bank again through Plaid Link (update mode, later).
RELINK_CODES = frozenset({"ITEM_LOGIN_REQUIRED", "PENDING_EXPIRATION", "PENDING_DISCONNECT"})

logger = logging.getLogger(__name__)

ItemStatus = Literal["ok", "needs_relink", "error"]

# Bump when the way transactions are imported changes; items synced with an older version are
# re-imported once from the start (their rows are updated in place by transaction_id).
# 2: the date is the purchase date (Plaid authorized_date), not the posting date.
# 3: the merchant is Plaid's name only when it agrees with the bank's text (services/names.py).
# 4: Plaid's travel categories map to Travel; charges imported as Transportation are moved once.
# 5: gyms and sporting goods map to Hobbies › Sports & Fitness; moved the same way.
# 6: Plaid's name must agree on a distinctive word, not a generic one ("City of Vancouver").
# 7: Plaid's name must agree with the bank's text both ways, on all its distinctive words
#    ("Triple O Construction") and on the bank's first word ("Whistler's Inn" for EARLS WHISTLER).
# 8: Plaid's VERY_HIGH merchant matches need only one shared word ("REAL CDN SUPERS").
# 9: Plaid's education category maps to Education › Tuition & Courses (was Kids › School Supplies).
# 10: electronics stores map to Household › Electronics & Appliances (was Other).
# 11: the relink merge recovers choices from soft-deleted copies; done once for all rows.
# 12: remembered categories count as choices when recovering, and are also found by the bank's
#     text; the recovery runs once more, and remembered categories are applied to existing rows.
# 13: rows record whether the user chose their category (edited); the others follow Plaid's
#     category when Plaid updates them. Once: existing rows are settled (stuck "Other" back to the
#     remembered or Plaid's category, other non-default choices marked edited) and the recovery
#     runs again, copying only real choices.
DATA_VERSION = 13
# Items synced before this version get the one-time settling of existing rows and recovery of
# choices (services/relink.py).
RECOVER_CHOICES_BELOW = 13
# Versions below this read the history again from the start; newer steps only fix rows in place.
REIMPORT_BELOW = 8
# Category mapping changes applied to charges already imported:
# (version that introduced it, Plaid category prefix, category and subcategory the old mapping gave).
CATEGORY_MOVES: tuple[tuple[int, str, str, str], ...] = (
    (4, "TRAVEL", "Transportation", ""),
    (5, "PERSONAL_CARE_GYMS_AND_FITNESS_CENTERS", "Personal Care", ""),
    (5, "GENERAL_MERCHANDISE_SPORTING_GOODS", "Other", ""),
    (9, "GENERAL_SERVICES_EDUCATION", "Kids", "School Supplies"),
    (10, "GENERAL_MERCHANDISE_ELECTRONICS", "Other", ""),
)
# "Sync" also asks Plaid to read the bank right away, at most this often per bank (Plaid bills
# /transactions/refresh per request outside the Trial plan).
REFRESH_INTERVAL = timedelta(hours=1)


class ItemNotFoundError(Exception):
    pass


class LinkedInstitution(BaseModel):
    item_id: str
    institution: str
    last_synced_at: str
    # When Plaid last read the bank (new transactions can't arrive before Plaid reads it again).
    bank_updated_at: str
    # The bank asked for a new sign-in: the app shows Reconnect.
    needs_reconnect: bool = False
    accounts: list[BankAccount]


class ItemSyncResult(BaseModel):
    item_id: str
    institution: str
    status: ItemStatus
    changes: BankChanges = BankChanges()
    error: str | None = None
    bank_updated_at: str = ""
    # True when this sync asked Plaid to read the bank now; new transactions follow shortly.
    refresh_requested: bool = False


class SyncResult(BaseModel):
    items: list[ItemSyncResult]


def to_transaction(
    plaid: PlaidTransaction,
    categories: list[Category],
    default_currency: str,
    rules: dict[str, MerchantRule] | None = None,
    names: dict[str, str] | None = None,
) -> BankTransaction:
    """A Plaid transaction as a sheet row. A category the user chose for the merchant (`rules`,
    by merchant_key) wins over Plaid's category, and a name learned from a matched receipt
    (`names`, by bank_key) wins over Plaid's and the bank's names."""
    category = plaid.personal_finance_category
    primary, detailed = (category.primary, category.detailed) if category else ("", "")
    mapped = map_category(primary, detailed, categories)
    currency = (plaid.iso_currency_code or plaid.unofficial_currency_code or default_currency).upper()
    base = display_name(plaid.merchant_name or "", plaid.name, plaid.merchant_confidence)
    name = (names or {}).get(bank_key(plaid.name), base)
    rule = find_rule(rules or {}, name, base, plaid.name, plaid.merchant_name or "")
    kind, category_name, subcategory = (
        (rule.kind, rule.category, rule.subcategory)
        if rule
        else (mapped.kind, mapped.category, mapped.subcategory)
    )
    return BankTransaction(
        transaction_id=plaid.transaction_id,
        account_id=plaid.account_id,
        # The purchase date, as banks and card statements show it; the posting date can be days later.
        date=plaid.authorized_date or plaid.date,
        merchant=name,
        description=plaid.name.strip(),
        amount=plaid.amount,
        currency=currency,
        kind=kind,
        category=category_name,
        subcategory=subcategory,
        plaid_category=detailed or primary,
        pending=plaid.pending,
    )


def find_rule(
    rules: dict[str, MerchantRule], name: str, base: str, description: str, plaid_merchant: str = ""
) -> MerchantRule | None:
    """The remembered category for a charge. Rules are saved under the merchant name shown when the
    user chose it, and the shown name can change (learned names, better name cleanup), so the
    charge's own bank text and Plaid's merchant name are tried too."""
    keys = [merchant_key(name), merchant_key(base), bank_key(description)]
    if plaid_merchant:
        keys.append(merchant_key(plaid_merchant))
    return next((rules[key] for key in keys if key in rules), None)


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


def create_link_token(
    provider: BankProvider, store: TokenStore, user_id: str, item_id: str | None = None
) -> str:
    """A Link token for linking a new bank, or with `item_id`, for signing in again to that linked
    bank (Plaid update mode). Reconnecting keeps the link: the next sync brings only what's new."""
    if item_id is None:
        return provider.create_link_token(user_id)
    item = next((i for i in store.list_items(user_id) if i.item_id == item_id), None)
    if item is None:
        raise ItemNotFoundError(item_id)
    return provider.create_link_token(user_id, item.access_token)


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
    with user_lock(user_id):
        item = next(i for i in store.list_items(user_id) if i.item_id == item_id)
        result = _sync_item(provider, store, repository, item, categories, default_currency, now)
        replace_old_links(provider, store, repository, user_id, categories, set(result.changes.added_ids))
        refresh_matches(repository)
    return result


def replace_old_links(
    provider: BankProvider,
    store: TokenStore,
    repository: Repository,
    user_id: str,
    categories: list[Category],
    fresh_ids: set[str],
    recover_all: bool = False,
) -> None:
    """A bank linked again replaces its older link (services/relink.py): merge the old accounts'
    transactions into the new ones, recover the user's choices from deleted copies for the rows
    just imported (`fresh_ids`, or all with `recover_all`), then unlink older links whose accounts
    were all replaced."""
    items = sorted(store.list_items(user_id), key=lambda i: (i.created_at, i.item_id), reverse=True)
    order = {item.item_id: index for index, item in enumerate(items)}
    merged = merge_replaced_accounts(repository, order, categories, fresh_ids, recover_all)
    if merged.paired or merged.moved or merged.dropped or merged.recovered:
        logger.info(
            "Merged a relinked bank: %d paired, %d moved, %d pending dropped, %d choices recovered",
            merged.paired,
            merged.moved,
            merged.dropped,
            merged.recovered,
        )
    for item_id in merged.replaced_items:
        try:
            unlink_item(provider, store, repository, user_id, item_id)
        except PlaidError as exc:
            logger.warning("Could not unlink a replaced bank link: %s", exc.code)


def list_linked(store: TokenStore, repository: Repository, user_id: str) -> list[LinkedInstitution]:
    accounts = repository.list_bank_accounts()
    return [
        LinkedInstitution(
            item_id=item.item_id,
            institution=item.institution_name,
            last_synced_at=item.last_synced_at,
            bank_updated_at=item.bank_updated_at,
            needs_reconnect=item.needs_reconnect,
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
    refresh: bool = False,
) -> SyncResult:
    """Import new, changed, and removed transactions for each of the user's linked items.

    With `refresh`, also ask Plaid to read each bank right away (at most once per
    REFRESH_INTERVAL per bank). Plaid reads it in the background, so the new transactions
    arrive in a later sync, usually within a minute.
    """
    with user_lock(user_id):  # two syncs at once could append the same new rows twice
        results = []
        items = store.list_items(user_id)
        recover_all = any(item.data_version < RECOVER_CHOICES_BELOW for item in items)
        for item in items:
            requested = refresh and _request_refresh(provider, store, item, now)
            result = _sync_item(provider, store, repository, item, categories, default_currency, now)
            results.append(result.model_copy(update={"refresh_requested": requested}))
        fresh = {transaction_id for r in results for transaction_id in r.changes.added_ids}
        if recover_all:
            _settle_existing_choices(repository, categories)
        replace_old_links(provider, store, repository, user_id, categories, fresh, recover_all)
        results = [r for r in results if r.item_id in {i.item_id for i in store.list_items(user_id)}]
        refresh_matches(repository)  # new charges may match receipts already saved
        return SyncResult(items=results)


def _request_refresh(provider: BankProvider, store: TokenStore, item: LinkedItem, now: str) -> bool:
    if item.refreshed_at and _parse(now) - _parse(item.refreshed_at) < REFRESH_INTERVAL:
        return False
    try:
        provider.refresh_transactions(item.access_token)
    except PlaidError:
        return False  # the sync below reports problems with the item
    store.mark_refreshed(item.user_id, item.item_id, now)
    return True


def _parse(timestamp: str) -> datetime:
    return datetime.fromisoformat(timestamp)


def _sync_item(
    provider: BankProvider,
    store: TokenStore,
    repository: Repository,
    item: LinkedItem,
    categories: list[Category],
    default_currency: str,
    now: str,
) -> ItemSyncResult:
    # An item imported the old way is read again from the start; rows are updated in place.
    start = item.cursor if item.data_version >= REIMPORT_BELOW else ""
    try:
        repository.save_bank_accounts(to_accounts(provider, item, default_currency))
        pages = _collect_pages(provider, item.access_token, start)
        rules = {rule.merchant: rule for rule in repository.list_merchant_rules()}
        names = repository.list_merchant_names()
        upserts = [
            to_transaction(t, categories, default_currency, rules, names)
            for page in pages
            for t in [*page.added, *page.modified]
        ]
        removed = [transaction_id for page in pages for transaction_id in page.removed]
        existing = (
            {t.transaction_id: t for t in repository.list_bank_transactions(BankTransactionFilter())}
            if any(page.modified for page in pages)
            else {}
        )
        changes = repository.apply_bank_changes(upserts, removed)
        repository.update_bank_fields(_follow_plaid(existing, upserts, categories, rules))
        moves = [move[1:] for move in CATEGORY_MOVES if item.data_version < move[0]]
        if moves:
            _move_charges(repository, categories, moves)
        bank_updated_at = provider.bank_updated_at(item.access_token)
    except PlaidError as exc:
        status: ItemStatus = "needs_relink" if exc.code in RELINK_CODES else "error"
        if status == "needs_relink":
            store.mark_needs_reconnect(item.user_id, item.item_id)
        return ItemSyncResult(
            item_id=item.item_id,
            institution=item.institution_name,
            status=status,
            error=exc.code,
            bank_updated_at=item.bank_updated_at,
        )
    store.save_cursor(
        item.user_id, item.item_id, pages[-1].next_cursor, now, DATA_VERSION, bank_updated_at or None
    )
    return ItemSyncResult(
        item_id=item.item_id,
        institution=item.institution_name,
        status="ok",
        changes=changes,
        bank_updated_at=bank_updated_at or item.bank_updated_at,
    )


def _choice(t: BankTransaction) -> tuple[str, str, str]:
    return t.kind, t.category, t.subcategory


def _import_choices(
    t: BankTransaction, categories: list[Category], rules: dict[str, MerchantRule]
) -> set[tuple[str, str, str]]:
    """What an import can have given this row: its remembered category, Plaid's mapped category,
    or "Other" (Plaid's category is often vague at first, e.g. for a pending charge)."""
    mapped = map_category(t.plaid_category, t.plaid_category, categories)
    choices: set[tuple[str, str, str]] = {
        (mapped.kind, mapped.category, mapped.subcategory),
        ("spending", OTHER, ""),
    }
    rule = find_rule(rules, t.merchant, t.merchant, t.description)
    if rule is not None:
        choices.add((rule.kind, rule.category, rule.subcategory))
    return choices


def _follow_plaid(
    existing: dict[str, BankTransaction],
    upserts: list[BankTransaction],
    categories: list[Category],
    rules: dict[str, MerchantRule],
) -> dict[str, dict[str, str]]:
    """Plaid often improves a charge's category after the first import. A row the user never chose a
    category for (not edited, and still showing what the import gave it) takes the new one;
    choices made in the app or in the spreadsheet are kept."""
    updates: dict[str, dict[str, str]] = {}
    for new in upserts:
        old = existing.get(new.transaction_id)
        if old is None or old.edited or _choice(old) == _choice(new):
            continue
        if _choice(old) in _import_choices(old, categories, rules):
            updates[new.transaction_id] = dict(
                zip(("kind", "category", "subcategory"), _choice(new), strict=True)
            )
    return updates


def _settle_existing_choices(repository: Repository, categories: list[Category]) -> None:
    """Version 13, once: rows from before the edited column. A row showing "Other" or Plaid's mapped
    category while its remembered category (found by name or bank text) or Plaid's mapped category
    is something else (stuck when Plaid improved its category, or a remembered category saved under
    an older name) gets that category. A row with any other category is the user's choice and is
    marked edited, so it's kept from now on."""
    rules = {rule.merchant: rule for rule in repository.list_merchant_rules()}
    updates: dict[str, dict[str, str]] = {}
    for t in repository.list_bank_transactions(BankTransactionFilter()):
        if t.edited:
            continue
        rule = find_rule(rules, t.merchant, t.merchant, t.description)
        mapped = map_category(t.plaid_category, t.plaid_category, categories)
        default = (
            (rule.kind, rule.category, rule.subcategory)
            if rule
            else (mapped.kind, mapped.category, mapped.subcategory)
        )
        choice = (t.kind, t.category, t.subcategory)
        if choice == default:
            continue
        if choice in {("spending", OTHER, ""), (mapped.kind, mapped.category, mapped.subcategory)}:
            updates[t.transaction_id] = dict(zip(("kind", "category", "subcategory"), default, strict=True))
        else:
            updates[t.transaction_id] = {"edited": "TRUE"}
    repository.update_bank_fields(updates)


def _move_charges(
    repository: Repository, categories: list[Category], moves: list[tuple[str, str, str]]
) -> None:
    """Move charges imported under an older mapping: a charge whose Plaid category starts with one
    of `moves`' prefixes and still shows exactly that old category and subcategory (and has no
    remembered category for the merchant) gets the current mapping. Charges the user changed are
    left alone."""
    rules = {rule.merchant for rule in repository.list_merchant_rules()}
    updates: dict[str, dict[str, str]] = {}
    for t in repository.list_bank_transactions(BankTransactionFilter()):
        plaid = t.plaid_category.upper()
        if t.kind != "spending" or merchant_key(t.merchant) in rules:
            continue
        if not any(
            plaid.startswith(prefix) and t.category == old and t.subcategory == old_sub
            for prefix, old, old_sub in moves
        ):
            continue
        mapped = map_category(plaid, plaid, categories)
        if mapped.category != t.category:
            updates[t.transaction_id] = {"category": mapped.category, "subcategory": mapped.subcategory}
    repository.update_bank_fields(updates)


def _collect_pages(provider: BankProvider, access_token: str, start: str) -> list[SyncPage]:
    """All pages from the `start` cursor. Restarts from it if Plaid reports a mid-sync change."""
    for attempt in range(MAX_SYNC_ATTEMPTS):
        pages: list[SyncPage] = []
        cursor = start
        try:
            while True:
                page = provider.sync_transactions(access_token, cursor)
                pages.append(page)
                cursor = page.next_cursor
                if not page.has_more:
                    return pages
        except PlaidError as exc:
            if exc.code != MUTATION_DURING_PAGINATION or attempt == MAX_SYNC_ATTEMPTS - 1:
                raise
    raise AssertionError("unreachable")
