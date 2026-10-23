"""Statement import (phase 11): accounts filled from files the user downloads from their bank.

A file is previewed first (nothing saved), then imported: rows are checked against the account's
earlier rows (duplicate keys and look-alikes), named and categorized like Plaid charges, appended in
one write, and matched to receipts. Each import is recorded so it can be undone.
"""

import base64
import binascii
import hashlib
import logging
import uuid
from collections import Counter
from collections.abc import Iterable
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from app.bank.categories import OTHER
from app.imports.categorizer import (
    CategorizeError,
    MerchantCategorizer,
    MerchantCategory,
    MerchantToCategorize,
)
from app.imports.parser import ColumnMapping, ImportFileError, ParsedRow, parse_statement
from app.imports.rules import keyword_kind
from app.services.bank import find_rule
from app.services.locks import user_lock
from app.services.matching import refresh_matches
from app.services.names import bank_key, clean_bank_text, merchant_key, name_words
from app.storage.models import (
    BankAccount,
    BankTransaction,
    BankTransactionFilter,
    BankTransactionKind,
    Category,
    ImportRecord,
)
from app.storage.repository import Repository

logger = logging.getLogger(__name__)

# A row that looks like an existing one (same amount, a shared word) within this many days is a
# possible duplicate: the bank may show the posting date in one file and the purchase date in another.
LOOKALIKE_DAYS = 3
# Imports before this time compared files only with the account's own rows, so a card Plaid had
# already imported could come in twice; plaid_duplicates cleans those up. Later imports were checked.
CROSS_ACCOUNT_CHECK_SINCE = "2026-10-23T19:42:00Z"

RowStatus = Literal["new", "duplicate", "possible_duplicate", "skipped"]
Choice = tuple[BankTransactionKind, str, str]


class AccountNotFoundError(Exception):
    pass


class LinkedAccountError(Exception):
    """Plaid keeps this account up to date; importing into it would duplicate the next sync."""


class ImportNotFoundError(Exception):
    pass


class AccountDraft(BaseModel):
    institution: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=100)
    type: Literal["depository", "credit"]
    mask: str = Field(default="", pattern=r"^\d{0,4}$")
    currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Za-z]{3}$")


class AccountInfo(BaseModel):
    account: BankAccount
    linked: bool
    last_import: ImportRecord | None = None


class PreviewRow(BaseModel):
    line: int
    date: date | None
    description: str
    amount: Decimal | None
    status: RowStatus
    reason: str = ""


class PreviewCounts(BaseModel):
    new: int = 0
    duplicate: int = 0
    possible_duplicate: int = 0
    skipped: int = 0


class ImportPreview(BaseModel):
    format: Literal["csv", "ofx"]
    columns: list[str]
    examples: list[str]
    mapping: ColumnMapping | None
    mapping_confident: bool
    rows: list[PreviewRow]
    counts: PreviewCounts
    date_from: date | None
    date_to: date | None


class ImportResult(BaseModel):
    import_id: str
    added: int
    duplicates: int
    skipped: int


# --- accounts ------------------------------------------------------------------------------------


def list_accounts(repository: Repository) -> list[AccountInfo]:
    latest: dict[str, ImportRecord] = {}
    for record in repository.list_imports():  # newest first
        latest.setdefault(record.account_id, record)
    return [
        AccountInfo(account=account, linked=account.linked, last_import=latest.get(account.account_id))
        for account in repository.list_bank_accounts()
    ]


def create_account(repository: Repository, draft: AccountDraft) -> BankAccount:
    account = BankAccount(
        account_id=str(uuid.uuid4()),
        item_id="",
        institution=draft.institution.strip(),
        name=draft.name.strip(),
        mask=draft.mask,
        type=draft.type,
        subtype="credit card" if draft.type == "credit" else "",
        currency=draft.currency.upper(),
    )
    repository.save_bank_accounts([account])
    return account


def update_account(repository: Repository, account_id: str, draft: AccountDraft) -> BankAccount:
    account = _file_account(repository, account_id)
    updated = account.model_copy(
        update={
            "institution": draft.institution.strip(),
            "name": draft.name.strip(),
            "mask": draft.mask,
            "type": draft.type,
            "subtype": "credit card" if draft.type == "credit" else "",
            "currency": draft.currency.upper(),
        }
    )
    repository.save_bank_accounts([updated])
    return updated


def delete_account(repository: Repository, account_id: str, remove_transactions: bool) -> None:
    _file_account(repository, account_id)
    repository.remove_bank_account(account_id)
    if remove_transactions:
        repository.remove_bank_transactions([account_id])


def _file_account(repository: Repository, account_id: str) -> BankAccount:
    account = next((a for a in repository.list_bank_accounts() if a.account_id == account_id), None)
    if account is None:
        raise AccountNotFoundError(account_id)
    if account.linked:
        raise LinkedAccountError(account_id)
    return account


# --- preview and import --------------------------------------------------------------------------


def decode_file(data: str) -> bytes:
    try:
        return base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ImportFileError("The file couldn't be read. Please choose it again.") from exc


class _Checked(BaseModel):
    row: ParsedRow
    status: RowStatus
    reason: str = ""
    key: str = ""


def _check(
    repository: Repository, account: BankAccount, data: bytes, chosen: ColumnMapping | None
) -> tuple[ImportPreview, list[_Checked]]:
    saved = _saved_mapping(account)
    parsed = parse_statement(data, account.type, saved=saved, chosen=chosen)
    if parsed.currency and parsed.currency != account.currency:
        raise ImportFileError(f"This file is in {parsed.currency}, but the account is in {account.currency}.")
    related = _same_card_accounts(repository, account)
    transactions = repository.list_bank_transactions(BankTransactionFilter())
    existing = [t for t in transactions if t.account_id == account.account_id]
    elsewhere = [t for t in transactions if t.account_id != account.account_id and t.account_id in related]
    checked = _classify(account.account_id, parsed.rows, existing, elsewhere, related)
    tally = Counter(c.status for c in checked)
    counts = PreviewCounts(
        new=tally["new"],
        duplicate=tally["duplicate"],
        possible_duplicate=tally["possible_duplicate"],
        skipped=tally["skipped"],
    )
    dates = [c.row.date for c in checked if c.row.date is not None and c.status != "skipped"]
    preview = ImportPreview(
        format=parsed.format,
        columns=parsed.columns,
        examples=parsed.examples,
        mapping=parsed.mapping,
        mapping_confident=parsed.mapping_confident,
        rows=[
            PreviewRow(
                line=c.row.line,
                date=c.row.date,
                description=c.row.description,
                amount=c.row.amount,
                status=c.status,
                reason=c.reason,
            )
            for c in checked
        ],
        counts=counts,
        date_from=min(dates, default=None),
        date_to=max(dates, default=None),
    )
    return preview, checked


def _saved_mapping(account: BankAccount) -> ColumnMapping | None:
    if not account.import_format:
        return None
    try:
        return ColumnMapping.model_validate_json(account.import_format)
    except ValidationError:
        logger.warning("Ignoring an unreadable import format on account %s", account.account_id)
        return None


def import_key(account_id: str, row: ParsedRow, repeat: int) -> str:
    """A row's identity across imports: the bank's own ID (OFX FITID), or a hash of what the row
    says; `repeat` counts identical rows earlier in the same file (two coffees on one day)."""
    if row.fitid:
        return f"ofx:{row.fitid}"
    text = " ".join(row.description.casefold().split())
    digest = hashlib.sha256(f"{account_id}|{row.date}|{row.amount}|{text}|{repeat}".encode()).hexdigest()
    return f"csv:{digest[:32]}"


def _same_card_accounts(repository: Repository, account: BankAccount) -> dict[str, BankAccount]:
    """Other accounts (linked through Plaid, unlinked, or added again by hand) that are probably the
    same card or bank account: the same type and last 4 digits, or, when either has no last 4 digits,
    the same type and bank. Their rows are look-alike candidates, so a card that Plaid imported
    isn't imported again from a file."""
    related: dict[str, BankAccount] = {}
    for other in [*repository.list_bank_accounts(), *repository.list_removed_bank_accounts()]:
        if other.account_id == account.account_id or other.type != account.type:
            continue
        if other.mask and account.mask:
            same = other.mask == account.mask
        else:
            same = bool(name_words(other.institution) & name_words(account.institution))
        if same:
            related[other.account_id] = other
    return related


def _account_label(account: BankAccount) -> str:
    # Plaid names often start with the bank already ("CIBC Aventura Visa").
    named = account.name.casefold().startswith(account.institution.casefold())
    name = account.name if named else " ".join(part for part in (account.institution, account.name) if part)
    return f"{name} •• {account.mask}" if account.mask else name


def _classify(
    account_id: str,
    rows: list[ParsedRow],
    existing: list[BankTransaction],
    elsewhere: list[BankTransaction] | None = None,
    accounts: dict[str, BankAccount] | None = None,
) -> list[_Checked]:
    """Each row's status. `existing` are the account's own rows (duplicate keys and look-alikes);
    `elsewhere` are rows of the same card in other `accounts` (look-alikes only)."""
    known = {t.import_key for t in existing if t.import_key}
    seen: Counter[tuple[object, ...]] = Counter()
    checked: list[_Checked] = []
    for row in rows:
        if row.problem:
            checked.append(_Checked(row=row, status="skipped", reason=row.problem))
            continue
        identity = (row.date, row.amount, " ".join(row.description.casefold().split()))
        key = import_key(account_id, row, seen[identity])
        seen[identity] += 1
        status: RowStatus = "duplicate" if key in known else "new"
        checked.append(_Checked(row=row, status=status, key=key))

    # Look-alikes: rows imported another way (Plaid) or from a file the bank wrote differently.
    matched_keys = {c.key for c in checked if c.status == "duplicate"}
    pool = _LookalikePool(
        t for t in [*existing, *(elsewhere or [])] if not (t.import_key and t.import_key in matched_keys)
    )
    for c in checked:
        if c.status != "new" or c.row.date is None or c.row.amount is None:
            continue
        twin = pool.take(c.row.date, c.row.amount, c.row.description)
        if twin is None:
            continue
        c.status = "possible_duplicate"
        c.reason = f"Looks like {twin.merchant or twin.description} on {twin.date.isoformat()}"
        other = (accounts or {}).get(twin.account_id)
        if twin.account_id != account_id and other is not None:
            c.reason += f" in {_account_label(other)}" + (" (from Plaid)" if not twin.import_id else "")
    return checked


class _LookalikePool:
    """Existing rows a new row may repeat: the same amount, a shared description word, and dates at
    most LOOKALIKE_DAYS apart. Each row is taken once."""

    def __init__(self, transactions: Iterable[BankTransaction]) -> None:
        self._by_amount: dict[Decimal, list[tuple[BankTransaction, set[str]]]] = {}
        for t in transactions:
            words = name_words(t.description) | name_words(t.merchant)
            self._by_amount.setdefault(t.amount, []).append((t, words))

    def take(self, when: date, amount: Decimal, description: str) -> BankTransaction | None:
        candidates = self._by_amount.get(amount, [])
        words = name_words(description)
        for entry in candidates:
            if abs((when - entry[0].date).days) <= LOOKALIKE_DAYS and words & entry[1]:
                candidates.remove(entry)
                return entry[0]
        return None


def preview_import(
    repository: Repository, account_id: str, data: bytes, mapping: ColumnMapping | None
) -> ImportPreview:
    account = _file_account(repository, account_id)
    return _check(repository, account, data, mapping)[0]


def run_import(
    repository: Repository,
    categorizer: MerchantCategorizer | None,
    user_id: str,
    account_id: str,
    filename: str,
    data: bytes,
    mapping: ColumnMapping | None,
    include_lines: set[int],
    now: str,
) -> ImportResult:
    """Import the file's new rows, plus the possible duplicates the user ticked (`include_lines`)."""
    with user_lock(user_id):
        account = _file_account(repository, account_id)
        preview, checked = _check(repository, account, data, mapping)
        chosen = [
            c
            for c in checked
            if c.status == "new" or (c.status == "possible_duplicate" and c.row.line in include_lines)
        ]
        import_id = str(uuid.uuid4())
        transactions = _to_transactions(repository, categorizer, account, chosen, import_id)
        repository.apply_bank_changes(transactions, [])
        dates = [t.date for t in transactions]
        repository.add_import(
            ImportRecord(
                import_id=import_id,
                account_id=account.account_id,
                filename=filename[:200],
                format=preview.format,
                date_from=min(dates, default=None),
                date_to=max(dates, default=None),
                added=len(transactions),
                duplicates=len(checked) - len(chosen) - preview.counts.skipped,
                skipped=preview.counts.skipped,
                created_at=now,
            )
        )
        if preview.mapping is not None:
            mapping_json = preview.mapping.model_dump_json()
            if mapping_json != account.import_format:
                repository.save_bank_accounts([account.model_copy(update={"import_format": mapping_json})])
        if transactions:
            refresh_matches(repository)  # new charges may match receipts already saved
        return ImportResult(
            import_id=import_id,
            added=len(transactions),
            duplicates=len(checked) - len(chosen) - preview.counts.skipped,
            skipped=preview.counts.skipped,
        )


def undo_import(repository: Repository, user_id: str, import_id: str) -> int:
    with user_lock(user_id):
        removed = repository.delete_import(import_id)
    if removed is None:
        raise ImportNotFoundError(import_id)
    return removed


def plaid_duplicates(repository: Repository) -> list[BankTransaction]:
    """Rows imported from statement files that repeat a charge Plaid imported for the same card
    (in a linked or unlinked account), from imports made before look-alikes were checked across
    accounts (CROSS_ACCOUNT_CHECK_SINCE). Each Plaid charge explains one imported row."""
    transactions = repository.list_bank_transactions(BankTransactionFilter())
    # Rows this cleanup already removed still account for their Plaid charge (rows removed by Undo
    # don't: their import is gone).
    imports = {r.import_id for r in repository.list_imports() if r.created_at < CROSS_ACCOUNT_CHECK_SINCE}
    removed = [t for t in repository.list_deleted_bank_transactions() if t.import_id in imports]
    found: list[BankTransaction] = []
    for account in repository.list_bank_accounts():
        if account.linked:
            continue
        related = _same_card_accounts(repository, account)
        plaid = {a for a, other in related.items() if other.item_id}
        pool = _LookalikePool(t for t in transactions if t.account_id in plaid and not t.import_id)
        for t in removed:
            if t.account_id == account.account_id:
                pool.take(t.date, t.amount, t.description)
        for t in sorted(transactions, key=lambda t: (t.date, t.transaction_id)):
            if (
                t.account_id == account.account_id
                and t.import_id in imports
                and pool.take(t.date, t.amount, t.description)
            ):
                found.append(t)
    return found


def remove_plaid_duplicates(repository: Repository, user_id: str) -> int:
    """Soft-delete the rows `plaid_duplicates` finds (the Plaid copies, with their history, receipt
    matches, and chosen categories, stay). Returns how many were removed."""
    with user_lock(user_id):
        duplicates = plaid_duplicates(repository)
        repository.update_bank_fields({t.transaction_id: {"deleted": "TRUE"} for t in duplicates})
        if duplicates:
            refresh_matches(repository)
    return len(duplicates)


# --- names and categories ------------------------------------------------------------------------


def _to_transactions(
    repository: Repository,
    categorizer: MerchantCategorizer | None,
    account: BankAccount,
    rows: list[_Checked],
    import_id: str,
) -> list[BankTransaction]:
    if not rows:
        return []
    categories = repository.list_categories()
    rules = {rule.merchant: rule for rule in repository.list_merchant_rules()}
    names = repository.list_merchant_names()
    history = _merchant_history(repository.list_bank_transactions(BankTransactionFilter()))

    prepared: list[tuple[_Checked, str, str, Choice | None]] = []
    for c in rows:
        assert c.row.amount is not None
        cleaned = clean_bank_text(c.row.description)
        merchant = names.get(bank_key(c.row.description), cleaned)
        rule = find_rule(rules, merchant, cleaned, c.row.description)
        choice: Choice | None = None
        if rule is not None:
            choice = (rule.kind, rule.category, rule.subcategory)
        else:
            choice = history.get(merchant_key(merchant)) or history.get(bank_key(c.row.description))
        if choice is None:
            kind = keyword_kind(c.row.description, c.row.amount, account.type)
            if kind is not None:
                choice = (kind, "", "")
        prepared.append((c, cleaned, merchant, choice))

    asked = _ask_claude(
        categorizer,
        account,
        [(merchant, c) for c, _, merchant, choice in prepared if choice is None],
        categories,
    )

    transactions: list[BankTransaction] = []
    for c, _, merchant, choice in prepared:
        assert c.row.date is not None and c.row.amount is not None
        kind, category, subcategory = (
            choice or asked.get(merchant_key(merchant)) or _fallback(c.row.amount, account.type)
        )
        transactions.append(
            BankTransaction(
                transaction_id=str(uuid.uuid4()),
                account_id=account.account_id,
                date=c.row.date,
                merchant=merchant,
                description=c.row.description,
                amount=c.row.amount,
                currency=account.currency,
                kind=kind,
                category=category,
                subcategory=subcategory,
                plaid_category="",
                pending=False,
                import_id=import_id,
                import_key=c.key,
            )
        )
    return transactions


def _merchant_history(transactions: list[BankTransaction]) -> dict[str, Choice]:
    """The latest category of each merchant's charges that the import (not the user) chose, by
    merchant key and bank key, so a merchant is categorized once rather than on every import."""
    history: dict[str, Choice] = {}
    for t in sorted(transactions, key=lambda t: t.date):
        choice: Choice = (t.kind, t.category, t.subcategory)
        # "Other" is what an import gives when it can't tell; such a merchant is asked about again.
        if t.edited or choice == ("spending", OTHER, ""):
            continue
        history[merchant_key(t.merchant or t.description)] = choice
        history[bank_key(t.description)] = choice
    return history


def _fallback(amount: Decimal, account_type: str) -> Choice:
    """No rule, no history, no answer from Claude: money out is spending in Other; money in is a
    refund on a card, income on a bank account."""
    if amount < 0 and account_type != "credit":
        return ("income", "", "")
    return ("spending", OTHER, "")


def _ask_claude(
    categorizer: MerchantCategorizer | None,
    account: BankAccount,
    merchants: list[tuple[str, _Checked]],
    categories: list[Category],
) -> dict[str, Choice]:
    """Claude's suggestions by merchant key, one question per merchant. Empty when Claude isn't
    configured or fails: the import still goes through, with the fallback categories."""
    if categorizer is None or not merchants:
        return {}
    by_key: dict[str, MerchantToCategorize] = {}
    for merchant, c in merchants:
        key = merchant_key(merchant)
        if key and key not in by_key:
            assert c.row.amount is not None
            by_key[key] = MerchantToCategorize(
                id=len(by_key),
                text=merchant,
                account_type="credit card" if account.type == "credit" else "bank account",
                money="out" if c.row.amount > 0 else "in",
            )
    keys = list(by_key)
    try:
        answers = categorizer.categorize(list(by_key.values()), categories)
    except CategorizeError as exc:
        logger.warning("Categorizing imported merchants failed: %s", exc)
        return {}
    found: dict[str, Choice] = {}
    for answer in answers:
        if 0 <= answer.id < len(keys):
            found[keys[answer.id]] = _valid_choice(answer, categories)
    return found


def _valid_choice(answer: MerchantCategory, categories: list[Category]) -> Choice:
    """Claude's answer with the sheet's spelling; unknown names fall back to Other / none."""
    if answer.kind != "spending":
        return (answer.kind, "", "")
    listed = {c.name.casefold(): c for c in categories}
    category = listed.get(answer.category.casefold()) or listed.get(OTHER.casefold())
    if category is None:
        return ("spending", OTHER, "")
    wanted = (answer.subcategory or "").casefold()
    subcategory = next((s for s in category.subcategories if s.casefold() == wanted), "")
    return ("spending", category.name, subcategory)
