"""Linking a bank again: the new link replaces the old one, without duplicate transactions.

Plaid gives a new link new account and transaction IDs, so linking a bank again (whether or not
the old link was unlinked first) would import every transaction a second time. An account of a
newer link "replaces" an account of an older or unlinked link with the same institution, type,
and last 4 digits. Its old transactions are merged into the new account:

- a pair (same amount, currency, and bank text, dates at most PAIR_DAYS apart) keeps the new row,
  which takes the user's choices from the old one (kind, category, subcategory, receipt match);
  the old row is soft-deleted;
- an old row without a pair is moved to the new account (history the new link doesn't return),
  unless it is pending: a pending charge of a link that no longer syncs never posts, so it is
  soft-deleted.

An older link that is still linked and whose accounts were all replaced is then unlinked. A second
login at the same bank with other accounts (e.g. a partner's) has other last 4 digits and is kept.
"""

from dataclasses import dataclass, field

from app.services.names import bank_key
from app.storage.models import BankAccount, BankTransaction, BankTransactionFilter
from app.storage.repository import Repository

# A pending charge can post a few days after its pending date.
PAIR_DAYS = 3
CARRIED_FIELDS = ("kind", "category", "subcategory", "receipt_id")


@dataclass
class MergeResult:
    paired: int = 0  # old rows whose new copy took over (old row soft-deleted)
    moved: int = 0  # old rows moved to the new account
    dropped: int = 0  # old pending rows soft-deleted
    replaced_items: set[str] = field(default_factory=set)  # older linked items fully replaced


def _account_key(account: BankAccount) -> tuple[str, str, str] | None:
    if not account.mask:
        return None  # without the last 4 digits, two accounts can't be told apart
    return account.institution.strip().casefold(), account.type, account.mask


def replacements(
    active: list[BankAccount], removed: list[BankAccount], item_order: dict[str, int]
) -> dict[str, str]:
    """{old account_id: new account_id}. `item_order` ranks the linked items, newest first; an
    account is replaced by the matching account of the newest linked item."""
    newest: dict[tuple[str, str, str], BankAccount] = {}
    for account in sorted(active, key=lambda a: item_order.get(a.item_id, len(item_order))):
        key = _account_key(account)
        if key is not None and key not in newest:
            newest[key] = account
    result: dict[str, str] = {}
    for account in [*active, *removed]:
        key = _account_key(account)
        new = newest.get(key) if key is not None else None
        if new is not None and new.account_id != account.account_id:
            result[account.account_id] = new.account_id
    return result


def _pair(
    old: list[BankTransaction], new: list[BankTransaction]
) -> list[tuple[BankTransaction, BankTransaction]]:
    """One-to-one pairs of the same charge, closest dates first."""
    candidates = [
        (abs((o.date - n.date).days), o.date, o.transaction_id, n.transaction_id, o, n)
        for o in old
        for n in new
        if o.amount == n.amount
        and o.currency == n.currency
        and abs((o.date - n.date).days) <= PAIR_DAYS
        and bank_key(o.description) == bank_key(n.description)
    ]
    pairs: list[tuple[BankTransaction, BankTransaction]] = []
    used_old: set[str] = set()
    used_new: set[str] = set()
    for *_, o, n in sorted(candidates, key=lambda c: c[:4]):
        if o.transaction_id in used_old or n.transaction_id in used_new:
            continue
        pairs.append((o, n))
        used_old.add(o.transaction_id)
        used_new.add(n.transaction_id)
    return pairs


def merge_replaced_accounts(repository: Repository, item_order: dict[str, int]) -> MergeResult:
    """Merge the transactions of replaced accounts into their new accounts (module docstring).
    Returns what changed, including the linked items whose accounts were all replaced (the caller
    unlinks them). Costs one read of each bank tab and at most two writes."""
    active = repository.list_bank_accounts()
    replaced = replacements(active, repository.list_removed_bank_accounts(), item_order)
    result = MergeResult()
    result.replaced_items = {
        item_id
        for item_id in {a.item_id for a in active}
        if all(a.account_id in replaced for a in active if a.item_id == item_id)
    }
    if not replaced:
        return result
    transactions = repository.list_bank_transactions(BankTransactionFilter())
    by_account: dict[str, list[BankTransaction]] = {}
    for t in transactions:
        by_account.setdefault(t.account_id, []).append(t)

    updates: dict[str, dict[str, str]] = {}
    removed: list[str] = []
    for old_id, new_id in replaced.items():
        old_rows = by_account.get(old_id, [])
        if not old_rows:
            continue
        pairs = _pair(old_rows, by_account.get(new_id, []))
        for old, new in pairs:
            carried = {name: str(getattr(old, name)) for name in CARRIED_FIELDS}
            if not carried["receipt_id"]:
                carried["receipt_id"] = new.receipt_id  # a match made on the new row since
            if any(str(getattr(new, name)) != value for name, value in carried.items()):
                updates[new.transaction_id] = carried
            removed.append(old.transaction_id)
        paired = {old.transaction_id for old, _ in pairs}
        for old in old_rows:
            if old.transaction_id in paired:
                continue
            if old.pending:
                removed.append(old.transaction_id)
                result.dropped += 1
            else:
                updates[old.transaction_id] = {"account_id": new_id}
                result.moved += 1
        result.paired += len(pairs)

    # Soft-delete first: a receipt carried to the new row must not stay on the old one too.
    repository.apply_bank_changes([], removed)
    repository.update_bank_fields(updates)
    return result
