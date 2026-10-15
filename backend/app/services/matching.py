"""Receipt matching (phase 10b): a scanned receipt and its card charge are the same purchase.

A matched charge keeps the receipt's ID in bank_transactions.receipt_id, and spending totals use
the receipt (with its items) instead of the charge, so the purchase counts once. Matching is
automatic; the user can undo a match (NO_RECEIPT, which is remembered) or pick a receipt by hand.
"""

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal

from app.services.scan import tolerance
from app.storage.models import NO_RECEIPT, BankTransaction, BankTransactionFilter, Receipt, ReceiptFilter
from app.storage.repository import Repository

# A charge posts on the purchase day or a few days later (rarely the day before, time zones).
DAYS_BEFORE = 1
DAYS_AFTER = 5
# Restaurants often charge the tip on the card but not on the itemized receipt.
TIP_CATEGORIES = frozenset({"dining out"})
MAX_TIP = Decimal("0.30")
# How far around a charge the "match a receipt" list looks.
CANDIDATE_DAYS = 7

_NOISE_WORDS = frozenset(
    {"the", "inc", "ltd", "store", "stores", "shop", "co", "corp", "com", "www", "pos", "sq"}
)


def _tokens(text: str) -> set[str]:
    """Words of a store or merchant name, lower-cased and accent-free, without numbers and noise."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return {word for word in re.split(r"[^a-z]+", plain) if len(word) > 1 and word not in _NOISE_WORDS}


def names_match(store: str, transaction: BankTransaction) -> bool:
    """FreshCo ↔ "FRESHCO #1234 TORONTO", Tim Hortons ↔ "TIM HORTONS 0567". Non-Latin store names
    (e.g. Japanese) have no comparable words, so they never count as a name match."""
    store_words = _tokens(store)
    return bool(store_words & (_tokens(transaction.merchant) | _tokens(transaction.description)))


def _days_apart(receipt: Receipt, transaction: BankTransaction) -> int:
    return (transaction.date - receipt.purchased_at).days


def _in_window(receipt: Receipt, transaction: BankTransaction) -> bool:
    return -DAYS_BEFORE <= _days_apart(receipt, transaction) <= DAYS_AFTER


def _is_tip_receipt(receipt: Receipt) -> bool:
    return any(item.category.casefold() in TIP_CATEGORIES for item in receipt.items)


@dataclass(frozen=True)
class Candidate:
    receipt: Receipt
    score: float


def match_score(receipt: Receipt, transaction: BankTransaction) -> float | None:
    """How well a receipt explains a charge, or None if it can't be the same purchase."""
    if (
        receipt.currency != transaction.currency
        or transaction.amount <= 0
        or not _in_window(receipt, transaction)
    ):
        return None
    same_name = names_match(receipt.store, transaction)
    if abs(transaction.amount - receipt.total) <= tolerance(receipt.currency):
        score = 10.0
    elif (
        same_name
        and _is_tip_receipt(receipt)
        and receipt.total < transaction.amount <= receipt.total * (1 + MAX_TIP)
    ):
        score = 6.0  # the card charge includes a tip
    else:
        return None
    if same_name:
        score += 3
    return score - abs(_days_apart(receipt, transaction)) * 0.5


def find_matches(receipts: list[Receipt], transactions: list[BankTransaction]) -> dict[str, str]:
    """New one-to-one matches {transaction_id: receipt_id} among unmatched receipts and charges.

    Only spending charges whose receipt_id is "" are considered; receipts already matched are
    skipped. Best-scoring pairs are taken first. An exact amount without a matching name is
    accepted only when it is the charge's single possible receipt and vice versa.
    """
    taken = {t.receipt_id for t in transactions if t.receipt_id and t.receipt_id != NO_RECEIPT}
    open_receipts = [r for r in receipts if r.receipt_id not in taken]
    open_charges = [t for t in transactions if t.kind == "spending" and not t.receipt_id]

    pairs: list[tuple[float, BankTransaction, Receipt]] = []
    for transaction in open_charges:
        for receipt in open_receipts:
            score = match_score(receipt, transaction)
            if score is not None:
                pairs.append((score, transaction, receipt))
    options_per_charge: dict[str, int] = {}
    options_per_receipt: dict[str, int] = {}
    for _, transaction, receipt in pairs:
        options_per_charge[transaction.transaction_id] = (
            options_per_charge.get(transaction.transaction_id, 0) + 1
        )
        options_per_receipt[receipt.receipt_id] = options_per_receipt.get(receipt.receipt_id, 0) + 1

    matches: dict[str, str] = {}
    used: set[str] = set()
    for _score, transaction, receipt in sorted(
        pairs, key=lambda p: (-p[0], p[1].transaction_id, p[2].receipt_id)
    ):
        if transaction.transaction_id in matches or receipt.receipt_id in used:
            continue
        named = names_match(receipt.store, transaction)
        unique = (
            options_per_charge[transaction.transaction_id] == 1
            and options_per_receipt[receipt.receipt_id] == 1
        )
        if not named and not unique:
            continue  # same amount, different-looking store, and other options: too uncertain
        matches[transaction.transaction_id] = receipt.receipt_id
        used.add(receipt.receipt_id)
    return matches


def refresh_matches(repository: Repository) -> int:
    """Drop matches to receipts that no longer exist, then add new automatic matches.

    Existing matches (automatic or chosen by the user) are kept while their receipt exists, and
    NO_RECEIPT is never overridden. Returns the number of changed charges.
    """
    transactions = repository.list_bank_transactions(BankTransactionFilter())
    if not transactions:
        return 0
    receipts = repository.list_receipts(ReceiptFilter())
    receipt_ids = {r.receipt_id for r in receipts}
    updates: dict[str, str] = {}
    for transaction in transactions:
        if transaction.receipt_id not in ("", NO_RECEIPT) and transaction.receipt_id not in receipt_ids:
            updates[transaction.transaction_id] = ""
    current = [
        t.model_copy(update={"receipt_id": updates.get(t.transaction_id, t.receipt_id)}) for t in transactions
    ]
    updates.update(find_matches(receipts, current))
    if updates:
        repository.set_bank_receipt_ids(updates)
    return len(updates)


class MatchError(Exception):
    pass


def candidates(repository: Repository, transaction_id: str) -> list[Candidate]:
    """Receipts that could explain a charge (same currency, within a week), best first."""
    transaction = next(
        (
            t
            for t in repository.list_bank_transactions(BankTransactionFilter())
            if t.transaction_id == transaction_id
        ),
        None,
    )
    if transaction is None:
        raise MatchError("Transaction not found")
    found: list[Candidate] = []
    for receipt in repository.list_receipts(ReceiptFilter()):
        if receipt.currency != transaction.currency:
            continue
        days = abs(_days_apart(receipt, transaction))
        if days > CANDIDATE_DAYS:
            continue
        score = match_score(receipt, transaction)
        difference = float(abs(transaction.amount - receipt.total))
        found.append(Candidate(receipt=receipt, score=score if score is not None else -difference - days))
    return sorted(found, key=lambda c: -c.score)[:10]


def set_match(repository: Repository, transaction_id: str, receipt_id: str | None) -> None:
    """Match a charge to a receipt chosen by the user, or (None) mark it as having no receipt.

    A receipt explains one charge: if it was matched to another charge, that one is unmatched.
    """
    transactions = repository.list_bank_transactions(BankTransactionFilter())
    if not any(t.transaction_id == transaction_id for t in transactions):
        raise MatchError("Transaction not found")
    if receipt_id is None:
        repository.set_bank_receipt_ids({transaction_id: NO_RECEIPT})
        return
    if not any(r.receipt_id == receipt_id for r in repository.list_receipts(ReceiptFilter())):
        raise MatchError("Receipt not found")
    updates = {
        t.transaction_id: ""
        for t in transactions
        if t.receipt_id == receipt_id and t.transaction_id != transaction_id
    }
    updates[transaction_id] = receipt_id
    repository.set_bank_receipt_ids(updates)
