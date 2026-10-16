import logging
import threading
import time
import uuid
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal, InvalidOperation

import requests
from google.auth.exceptions import GoogleAuthError as GoogleLibraryAuthError
from gspread.exceptions import APIError

from app.config import DEFAULT_CURRENCY
from app.drive.receipts import upload_receipt_image
from app.sheets.api import SheetsApi
from app.sheets.schema import FALSE, TABS, TRUE, tab_range, utc_now_iso
from app.storage.models import (
    BankAccount,
    BankChanges,
    BankTransaction,
    BankTransactionFilter,
    Category,
    Item,
    ItemDraft,
    MerchantRule,
    Receipt,
    ReceiptDraft,
    ReceiptFilter,
)
from app.storage.repository import StorageError

logger = logging.getLogger(__name__)

Record = dict[str, str]

READ_CACHE_TTL_SECONDS = 60.0
READ_CACHE_MAX_ENTRIES = 2_000

GOOGLE_ERRORS = (APIError, requests.RequestException, GoogleLibraryAuthError)


@dataclass(frozen=True)
class SheetRow:
    number: int  # 1-based row number in the sheet
    cells: list[str]  # raw cells, so columns the app doesn't know about are kept on rewrite
    values: Record  # cells keyed by header


@dataclass(frozen=True)
class TabData:
    headers: list[str]
    rows: list[SheetRow]


class TabReadCache:
    """Recent tab reads per spreadsheet, kept in server memory to save Sheets read quota.

    Edits made directly in the spreadsheet show up after at most the TTL.
    """

    def __init__(self, ttl_seconds: float = READ_CACHE_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._entries: dict[tuple[str, str], tuple[float, TabData]] = {}
        self._lock = threading.Lock()

    def get(self, spreadsheet_id: str, tab: str) -> TabData | None:
        with self._lock:
            entry = self._entries.get((spreadsheet_id, tab))
        if entry is None or time.monotonic() - entry[0] > self._ttl:
            return None
        return entry[1]

    def put(self, spreadsheet_id: str, tab: str, data: TabData) -> None:
        with self._lock:
            if len(self._entries) >= READ_CACHE_MAX_ENTRIES:
                self._entries.clear()
            self._entries[(spreadsheet_id, tab)] = (time.monotonic(), data)

    def invalidate(self, spreadsheet_id: str, tab: str) -> None:
        with self._lock:
            self._entries.pop((spreadsheet_id, tab), None)


tab_read_cache = TabReadCache()


class SheetsRepository:
    def __init__(self, api: SheetsApi, spreadsheet_id: str, cache: TabReadCache = tab_read_cache) -> None:
        self._api = api
        self._spreadsheet_id = spreadsheet_id
        self._cache = cache

    # --- categories ----------------------------------------------------------

    def list_categories(self) -> list[Category]:
        subcategories: dict[str, list[str]] = {}
        descriptions: dict[str, dict[str, str]] = {}
        for row in _active(self._read("subcategories")):
            category = row.values.get("category", "").strip().casefold()
            name = row.values.get("name", "").strip()
            names = subcategories.setdefault(category, [])
            if category and name and name.casefold() not in {n.casefold() for n in names}:
                names.append(name)
                description = row.values.get("description", "").strip()
                if description:
                    descriptions.setdefault(category, {})[name] = description
        categories: list[Category] = []
        for row in _active(self._read("categories")):
            name = row.values.get("name", "").strip()
            if name:
                categories.append(
                    Category(
                        name=name,
                        icon=row.values.get("icon", "").strip(),
                        subcategories=subcategories.get(name.casefold(), []),
                        descriptions=descriptions.get(name.casefold(), {}),
                    )
                )
        return categories

    # --- receipts ------------------------------------------------------------

    def list_receipts(self, filters: ReceiptFilter) -> list[Receipt]:
        receipts = self._load_receipts(self._read("receipts"), self._read("items"))
        selected = [
            receipt
            for receipt in receipts
            if (filters.date_from is None or receipt.purchased_at >= filters.date_from)
            and (filters.date_to is None or receipt.purchased_at <= filters.date_to)
            and (not filters.filters_items or any(filters.matches_item(item) for item in receipt.items))
        ]
        return sorted(selected, key=lambda r: (r.purchased_at, r.created_at), reverse=True)

    def get_receipt(self, receipt_id: str) -> Receipt | None:
        receipts = self._load_receipts(self._read("receipts"), self._read("items"))
        return next((receipt for receipt in receipts if receipt.receipt_id == receipt_id), None)

    def create_receipt(self, draft: ReceiptDraft) -> Receipt:
        now = utc_now_iso()
        receipt_id = str(uuid.uuid4())
        receipt_record = _receipt_record(receipt_id, draft, created_at=now, updated_at=now)
        item_records = [_item_record(str(uuid.uuid4()), receipt_id, item, now) for item in draft.items]

        receipts_tab = self._read("receipts")
        items_tab = self._read("items")
        self._append("receipts", receipts_tab.headers, [receipt_record])
        self._append("items", items_tab.headers, item_records)
        return _build_receipt(receipt_record, item_records)

    def update_receipt(self, receipt_id: str, draft: ReceiptDraft) -> Receipt | None:
        # Fresh reads: row numbers from the cache may be stale if the sheet was edited.
        receipts_tab = self._read("receipts", fresh=True)
        items_tab = self._read("items", fresh=True)
        receipt_row = _find_active(receipts_tab, "receipt_id", receipt_id)
        if receipt_row is None:
            return None

        now = utc_now_iso()
        receipt_record = _receipt_record(
            receipt_id,
            draft,
            created_at=receipt_row.values.get("created_at", ""),
            updated_at=now,
            image_link=receipt_row.values.get("image_link", ""),
        )
        writes: list[tuple[str, SheetRow]] = [
            (
                "receipts",
                _with_values(receipt_row, receipts_tab.headers, receipt_record),
            )
        ]

        existing = {
            row.values["item_id"]: row
            for row in _active(items_tab)
            if row.values.get("receipt_id") == receipt_id and row.values.get("item_id")
        }
        kept: list[Record] = []
        new_records: list[Record] = []
        for item in draft.items:
            if item.item_id and item.item_id in existing:
                record = _item_record(item.item_id, receipt_id, item, now)
                writes.append(
                    (
                        "items",
                        _with_values(existing[item.item_id], items_tab.headers, record),
                    )
                )
                kept.append(record)
            else:
                record = _item_record(str(uuid.uuid4()), receipt_id, item, now)
                new_records.append(record)

        kept_ids = {record["item_id"] for record in kept}
        for item_id, row in existing.items():
            if item_id not in kept_ids:
                writes.append(
                    (
                        "items",
                        _with_values(row, items_tab.headers, {"deleted": TRUE, "updated_at": now}),
                    )
                )

        self._write_rows(writes)
        self._append("items", items_tab.headers, new_records)
        return _build_receipt(receipt_record, kept + new_records)

    def attach_receipt_image(self, receipt_id: str, data: bytes, media_type: str) -> Receipt | None:
        receipts_tab = self._read("receipts", fresh=True)
        receipt_row = _find_active(receipts_tab, "receipt_id", receipt_id)
        if receipt_row is None:
            return None
        items_tab = self._read("items")
        receipt = next(r for r in self._load_receipts(receipts_tab, items_tab) if r.receipt_id == receipt_id)

        try:
            link = upload_receipt_image(self._api, receipt, data, media_type)
        except GOOGLE_ERRORS as exc:
            raise StorageError("Could not upload the receipt photo to Google Drive") from exc

        updates = {"image_link": link, "updated_at": utc_now_iso()}
        self._write_rows([("receipts", _with_values(receipt_row, receipts_tab.headers, updates))])
        return receipt.model_copy(update=updates)

    def delete_receipt(self, receipt_id: str) -> bool:
        receipts_tab = self._read("receipts", fresh=True)
        items_tab = self._read("items", fresh=True)
        receipt_row = _find_active(receipts_tab, "receipt_id", receipt_id)
        if receipt_row is None:
            return False

        now = utc_now_iso()
        deleted = {"deleted": TRUE, "updated_at": now}
        writes = [("receipts", _with_values(receipt_row, receipts_tab.headers, deleted))]
        writes += [
            ("items", _with_values(row, items_tab.headers, deleted))
            for row in _active(items_tab)
            if row.values.get("receipt_id") == receipt_id
        ]
        self._write_rows(writes)
        return True

    # --- bank and card linking --------------------------------------------------

    def list_bank_accounts(self) -> list[BankAccount]:
        return [_build_account(row.values) for row in _active(self._read("bank_accounts"))]

    def save_bank_accounts(self, accounts: list[BankAccount]) -> None:
        tab = self._read("bank_accounts", fresh=True)
        by_id = {row.values.get("account_id"): row for row in tab.rows}
        now = utc_now_iso()
        writes: list[tuple[str, SheetRow]] = []
        new_records: list[Record] = []
        for account in accounts:
            record = _account_record(account, now)
            row = by_id.get(account.account_id)
            if row is None:
                new_records.append(record)
            elif any(row.values.get(k, "") != v for k, v in record.items() if k != "updated_at"):
                writes.append(("bank_accounts", _with_values(row, tab.headers, record)))
        self._write_rows(writes)
        self._append("bank_accounts", tab.headers, new_records)

    def remove_bank_accounts(self, item_id: str) -> None:
        tab = self._read("bank_accounts", fresh=True)
        deleted = {"deleted": TRUE, "updated_at": utc_now_iso()}
        self._write_rows(
            [
                ("bank_accounts", _with_values(row, tab.headers, deleted))
                for row in _active(tab)
                if row.values.get("item_id") == item_id
            ]
        )

    def apply_bank_changes(self, upserts: list[BankTransaction], removed_ids: list[str]) -> BankChanges:
        if not upserts and not removed_ids:
            return BankChanges()
        tab = self._read("bank_transactions", fresh=True)
        by_id = {row.values.get("transaction_id"): row for row in tab.rows}
        now = utc_now_iso()
        changes = BankChanges()
        writes: list[tuple[str, SheetRow]] = []
        new_records: list[Record] = []
        for transaction in upserts:
            row = by_id.get(transaction.transaction_id)
            if row is None:
                record = _transaction_record(transaction, now)
                new_records.append(record)
                by_id[transaction.transaction_id] = SheetRow(number=-1, cells=[], values=record)
                changes.added += 1
            elif row.number > 0:
                # Keep what the user may have changed; a row the user deleted stays deleted.
                bank_fields = {
                    k: v for k, v in _transaction_record(transaction, now).items() if k not in USER_FIELDS
                }
                # Unchanged rows aren't rewritten (a full re-import would otherwise rewrite them all).
                if any(row.values.get(k, "") != v for k, v in bank_fields.items() if k != "updated_at"):
                    writes.append(("bank_transactions", _with_values(row, tab.headers, bank_fields)))
                    changes.modified += 1
        deleted = {"deleted": TRUE, "updated_at": now}
        for transaction_id in removed_ids:
            row = by_id.get(transaction_id)
            if row is not None and row.number > 0 and row.values.get("deleted", "").upper() != TRUE:
                writes.append(("bank_transactions", _with_values(row, tab.headers, deleted)))
                changes.removed += 1
        self._write_rows(writes)
        self._append("bank_transactions", tab.headers, new_records)
        return changes

    def set_bank_receipt_ids(self, updates: dict[str, str]) -> None:
        self.update_bank_fields(
            {transaction_id: {"receipt_id": value} for transaction_id, value in updates.items()}
        )

    def update_bank_fields(self, updates: dict[str, dict[str, str]]) -> None:
        if not updates:
            return
        tab = self._read("bank_transactions", fresh=True)
        now = utc_now_iso()
        self._write_rows(
            [
                (
                    "bank_transactions",
                    _with_values(
                        row, tab.headers, {**updates[row.values["transaction_id"]], "updated_at": now}
                    ),
                )
                for row in _active(tab)
                if row.values.get("transaction_id") in updates
            ]
        )

    def list_merchant_rules(self) -> list[MerchantRule]:
        rules: dict[str, MerchantRule] = {}
        for row in _active(self._read("merchant_categories")):
            merchant = row.values.get("merchant", "").strip()
            kind = row.values.get("kind", "").strip().lower()
            if merchant and kind in KINDS:
                rules[merchant] = MerchantRule(
                    merchant=merchant,
                    kind=kind,  # type: ignore[arg-type]
                    category=row.values.get("category", "").strip(),
                    subcategory=row.values.get("subcategory", "").strip(),
                )
        return list(rules.values())  # a later row for the same merchant wins

    def save_merchant_rule(self, rule: MerchantRule) -> None:
        tab = self._read("merchant_categories", fresh=True)
        record = {
            "merchant": rule.merchant,
            "kind": rule.kind,
            "category": rule.category,
            "subcategory": rule.subcategory,
            "updated_at": utc_now_iso(),
            "deleted": FALSE,
        }
        existing = [row for row in _active(tab) if row.values.get("merchant", "").strip() == rule.merchant]
        if existing:
            self._write_rows([("merchant_categories", _with_values(existing[-1], tab.headers, record))])
        else:
            self._append("merchant_categories", tab.headers, [record])

    def remove_bank_transactions(self, account_ids: list[str]) -> int:
        tab = self._read("bank_transactions", fresh=True)
        deleted = {"deleted": TRUE, "updated_at": utc_now_iso()}
        writes = [
            ("bank_transactions", _with_values(row, tab.headers, deleted))
            for row in _active(tab)
            if row.values.get("account_id") in set(account_ids)
        ]
        self._write_rows(writes)
        return len(writes)

    def list_bank_transactions(self, filters: BankTransactionFilter) -> list[BankTransaction]:
        transactions: list[BankTransaction] = []
        for row in _active(self._read("bank_transactions")):
            try:
                transaction = _build_transaction(row.values)
            except (ValueError, InvalidOperation):
                logger.warning("Skipping unreadable bank transaction row %d", row.number)
                continue
            if filters.date_from and transaction.date < filters.date_from:
                continue
            if filters.date_to and transaction.date > filters.date_to:
                continue
            if filters.account_id and transaction.account_id != filters.account_id:
                continue
            if filters.category and transaction.category.casefold() != filters.category.strip().casefold():
                continue
            transactions.append(transaction)
        return sorted(transactions, key=lambda t: (t.date, t.transaction_id), reverse=True)

    # --- reading -------------------------------------------------------------

    def _load_receipts(self, receipts_tab: TabData, items_tab: TabData) -> list[Receipt]:
        items_by_receipt: dict[str, list[Record]] = {}
        for row in _active(items_tab):
            items_by_receipt.setdefault(row.values.get("receipt_id", ""), []).append(row.values)

        receipts: list[Receipt] = []
        for row in _active(receipts_tab):
            receipt_id = row.values.get("receipt_id", "")
            try:
                receipts.append(_build_receipt(row.values, items_by_receipt.get(receipt_id, [])))
            except (ValueError, InvalidOperation):
                logger.warning("Skipping unreadable receipt row %d (%s)", row.number, receipt_id)
        return receipts

    def _read(self, tab: str, fresh: bool = False) -> TabData:
        """All data rows of a tab. Values are keyed by header, so column order doesn't matter."""
        if not fresh:
            cached = self._cache.get(self._spreadsheet_id, tab)
            if cached is not None:
                return cached

        try:
            rows = self._api.read_values(self._spreadsheet_id, tab_range(tab))
        except GOOGLE_ERRORS as exc:
            raise StorageError(f"Could not read the {tab} tab") from exc

        headers = [header.strip() for header in rows[0]] if rows else []
        data_rows = [
            SheetRow(
                number=index + 2,
                cells=row,
                values={h: (row[i] if i < len(row) else "") for i, h in enumerate(headers) if h},
            )
            for index, row in enumerate(rows[1:])
            if any(cell.strip() for cell in row)
        ]
        data = TabData(headers=headers, rows=data_rows)
        self._cache.put(self._spreadsheet_id, tab, data)
        return data

    # --- writing -------------------------------------------------------------

    def _append(self, tab: str, headers: list[str], records: list[Record]) -> None:
        if not records:
            return
        _require_columns(tab, headers)
        rows = [[record.get(header, "") for header in headers] for record in records]
        try:
            self._api.append_values(self._spreadsheet_id, tab_range(tab), rows)
        except GOOGLE_ERRORS as exc:
            raise StorageError(f"Could not add rows to the {tab} tab") from exc
        finally:
            self._cache.invalidate(self._spreadsheet_id, tab)

    def _write_rows(self, writes: list[tuple[str, SheetRow]]) -> None:
        """Rewrite whole rows in place, all in one API call."""
        if not writes:
            return
        data = [(tab_range(tab, f"A{row.number}"), [row.cells]) for tab, row in writes]
        try:
            self._api.write_values(self._spreadsheet_id, data)
        except GOOGLE_ERRORS as exc:
            raise StorageError("Could not update rows") from exc
        finally:
            for tab in {tab for tab, _ in writes}:
                self._cache.invalidate(self._spreadsheet_id, tab)


# --- row helpers ---------------------------------------------------------------


def _active(tab: TabData) -> list[SheetRow]:
    return [row for row in tab.rows if row.values.get("deleted", "").strip().upper() != TRUE]


def _find_active(tab: TabData, key: str, value: str) -> SheetRow | None:
    return next((row for row in _active(tab) if row.values.get(key) == value), None)


def _require_columns(tab: str, headers: list[str]) -> None:
    missing = [column for column in TABS[tab] if column not in headers]
    if missing:
        raise StorageError(f"The {tab} tab is missing columns {missing}; signing in again repairs it")


def _with_values(row: SheetRow, headers: list[str], updates: Record) -> SheetRow:
    """A copy of the row with some columns changed. Other cells are kept as they are."""
    cells = list(row.cells) + [""] * max(0, len(headers) - len(row.cells))
    for column, value in updates.items():
        if column not in headers:
            raise StorageError(f"Column {column} is missing; signing in again repairs it")
        cells[headers.index(column)] = value
    return replace(row, cells=cells, values={**row.values, **updates})


def _money(value: Decimal) -> str:
    return format(value, "f")


def _receipt_record(
    receipt_id: str, draft: ReceiptDraft, created_at: str, updated_at: str, image_link: str = ""
) -> Record:
    return {
        "receipt_id": receipt_id,
        "store": draft.store,
        "purchased_at": draft.purchased_at.isoformat(),
        "total": _money(draft.total),
        "currency": draft.currency,
        "image_link": image_link,
        "created_at": created_at,
        "updated_at": updated_at,
        "deleted": FALSE,
    }


def _item_record(item_id: str, receipt_id: str, item: ItemDraft, now: str) -> Record:
    return {
        "item_id": item_id,
        "receipt_id": receipt_id,
        "name": item.name,
        "quantity": _money(item.quantity),
        "price": _money(item.price),
        "category": item.category,
        "subcategory": item.subcategory or "",
        "updated_at": now,
        "deleted": FALSE,
    }


def _parse_decimal(text: str, default: Decimal | None = None) -> Decimal:
    cleaned = text.strip().replace(",", "")
    if not cleaned and default is not None:
        return default
    value = Decimal(cleaned)
    if not value.is_finite():
        raise ValueError(f"Not a finite number: {text!r}")
    return value


def _build_receipt(record: Record, item_records: list[Record]) -> Receipt:
    items: list[Item] = []
    for item in item_records:
        try:
            items.append(
                Item(
                    item_id=item["item_id"],
                    name=item.get("name", ""),
                    quantity=_parse_decimal(item.get("quantity", ""), default=Decimal(1)),
                    price=_parse_decimal(item.get("price", "")),
                    category=item.get("category", ""),
                    subcategory=item.get("subcategory", "").strip(),
                )
            )
        except (KeyError, ValueError, InvalidOperation):
            logger.warning("Skipping unreadable item %s", item.get("item_id", "?"))

    return Receipt(
        receipt_id=record["receipt_id"],
        store=record.get("store", ""),
        purchased_at=date.fromisoformat(record.get("purchased_at", "").strip()),
        total=_parse_decimal(record.get("total", "")),
        currency=(record.get("currency", "").strip().upper() or DEFAULT_CURRENCY),
        image_link=record.get("image_link", ""),
        created_at=record.get("created_at", ""),
        updated_at=record.get("updated_at", ""),
        items=items,
    )


# --- bank rows ----------------------------------------------------------------

# Columns the user may edit in the sheet (or the app sets itself); a bank update keeps them.
USER_FIELDS = frozenset({"kind", "category", "subcategory", "receipt_id", "deleted"})
KINDS = ("spending", "income", "transfer")


def _account_record(account: BankAccount, now: str) -> Record:
    return {
        "account_id": account.account_id,
        "item_id": account.item_id,
        "institution": account.institution,
        "name": account.name,
        "mask": account.mask,
        "type": account.type,
        "subtype": account.subtype,
        "currency": account.currency,
        "updated_at": now,
        "deleted": FALSE,
    }


def _build_account(record: Record) -> BankAccount:
    return BankAccount(
        account_id=record.get("account_id", ""),
        item_id=record.get("item_id", ""),
        institution=record.get("institution", ""),
        name=record.get("name", ""),
        mask=record.get("mask", ""),
        type=record.get("type", ""),
        subtype=record.get("subtype", ""),
        currency=(record.get("currency", "").strip().upper() or DEFAULT_CURRENCY),
    )


def _transaction_record(transaction: BankTransaction, now: str) -> Record:
    return {
        "transaction_id": transaction.transaction_id,
        "account_id": transaction.account_id,
        "date": transaction.date.isoformat(),
        "merchant": transaction.merchant,
        "description": transaction.description,
        "amount": _money(transaction.amount),
        "currency": transaction.currency,
        "kind": transaction.kind,
        "category": transaction.category,
        "subcategory": transaction.subcategory,
        "plaid_category": transaction.plaid_category,
        "pending": TRUE if transaction.pending else FALSE,
        "receipt_id": transaction.receipt_id,
        "updated_at": now,
        "deleted": FALSE,
    }


def _build_transaction(record: Record) -> BankTransaction:
    kind = record.get("kind", "").strip().lower()
    return BankTransaction(
        transaction_id=record.get("transaction_id", ""),
        account_id=record.get("account_id", ""),
        date=date.fromisoformat(record.get("date", "").strip()),
        merchant=record.get("merchant", ""),
        description=record.get("description", ""),
        amount=_parse_decimal(record.get("amount", "")),
        currency=(record.get("currency", "").strip().upper() or DEFAULT_CURRENCY),
        kind=kind if kind in KINDS else "spending",  # type: ignore[arg-type]
        category=record.get("category", "").strip(),
        subcategory=record.get("subcategory", "").strip(),
        plaid_category=record.get("plaid_category", ""),
        pending=record.get("pending", "").strip().upper() == TRUE,
        receipt_id=record.get("receipt_id", "").strip(),
    )
