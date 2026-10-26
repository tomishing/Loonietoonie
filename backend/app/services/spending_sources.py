"""All spending in one list: receipts plus bank and card charges that no receipt explains.

Patterns, Ask, and CSV export work on receipts. A charge matched to a receipt is left out (the
receipt and its items already count); every other spending charge becomes a one-item record
with receipt_id "bank:<transaction_id>", the merchant as store, and its category. Transfers and
income never count.

What the card was charged is what was spent: when a receipt's matched charges (same currency)
add up to more or less than the receipt, e.g. a tip added on the card, the receipt counts at the
charged amount. The difference is an extra item in the receipt's main category.
"""

from decimal import Decimal

from app.sheets.schema import TAX_CATEGORY
from app.storage.models import (
    BANK_RECEIPT_PREFIX,
    NO_RECEIPT,
    BankTransaction,
    BankTransactionFilter,
    Item,
    Receipt,
    ReceiptFilter,
)
from app.storage.repository import Repository

UNCATEGORIZED = "Other"
# Item ID prefix of the difference between a receipt and its card charges (not an item bought).
CHARGE_DIFFERENCE_PREFIX = "charged:"
CHARGE_DIFFERENCE_NAME = "Charged on the card (e.g. tip)"


def is_bank_record(receipt: Receipt) -> bool:
    return receipt.receipt_id.startswith(BANK_RECEIPT_PREFIX)


def bank_record(transaction: BankTransaction) -> Receipt:
    name = transaction.merchant or transaction.description or "Card or bank charge"
    return Receipt(
        receipt_id=BANK_RECEIPT_PREFIX + transaction.transaction_id,
        store=name,
        purchased_at=transaction.date,
        total=transaction.amount,
        currency=transaction.currency,
        image_link="",
        created_at="",
        updated_at="",
        items=[
            Item(
                item_id=transaction.transaction_id,
                name=name,
                quantity=Decimal(1),
                price=transaction.amount,
                category=transaction.category or UNCATEGORIZED,
                subcategory=transaction.subcategory,
            )
        ],
    )


def unexplained_charges(transactions: list[BankTransaction], receipt_ids: set[str]) -> list[BankTransaction]:
    """Spending charges that no existing receipt explains."""
    return [
        t
        for t in transactions
        if t.kind == "spending" and (t.receipt_id in ("", NO_RECEIPT) or t.receipt_id not in receipt_ids)
    ]


def _main_item(receipt: Receipt) -> Item | None:
    """The item of the category (and subcategory) with the most spending on the receipt; tax only
    when there is nothing else."""
    items = [i for i in receipt.items if i.category != TAX_CATEGORY] or receipt.items
    sums: dict[tuple[str, str], Decimal] = {}
    for item in items:
        key = (item.category, item.subcategory)
        sums[key] = sums.get(key, Decimal(0)) + item.quantity * item.price
    if not sums:
        return None
    category, subcategory = max(sums, key=lambda key: sums[key])
    return next(i for i in items if (i.category, i.subcategory) == (category, subcategory))


def charge_differences(receipts: list[Receipt], transactions: list[BankTransaction]) -> dict[str, Item]:
    """{receipt_id: an item for what its matched charges add beyond the receipt (or take off)}.
    Receipts whose charges are in another currency, or add up to the receipt, have none."""
    charged: dict[str, list[BankTransaction]] = {}
    for t in transactions:
        if t.kind == "spending" and t.receipt_id not in ("", NO_RECEIPT):
            charged.setdefault(t.receipt_id, []).append(t)
    differences: dict[str, Item] = {}
    for receipt in receipts:
        charges = charged.get(receipt.receipt_id)
        if not charges or any(t.currency != receipt.currency for t in charges):
            continue
        difference = sum((t.amount for t in charges), Decimal(0)) - receipt.total
        main = _main_item(receipt)
        if difference == 0 or main is None:
            continue
        differences[receipt.receipt_id] = Item(
            item_id=CHARGE_DIFFERENCE_PREFIX + receipt.receipt_id,
            name=CHARGE_DIFFERENCE_NAME,
            quantity=Decimal(1),
            price=difference,
            category=main.category,
            subcategory=main.subcategory,
        )
    return differences


def _with_difference(receipt: Receipt, difference: Item | None, filters: ReceiptFilter) -> Receipt:
    if difference is None or (filters.filters_items and not filters.matches_item(difference)):
        return receipt
    return receipt.model_copy(
        update={"total": receipt.total + difference.price, "items": [*receipt.items, difference]}
    )


def spending_records(repository: Repository, filters: ReceiptFilter | None = None) -> list[Receipt]:
    """Receipts and unexplained bank/card spending, newest first, with the same filters as receipts.

    With a category or subcategory filter, a charge is included when its category matches.
    Receipts count at what their matched charges add up to (module docstring).
    """
    filters = filters or ReceiptFilter()
    all_receipts = repository.list_receipts(ReceiptFilter())
    receipts = all_receipts if filters == ReceiptFilter() else repository.list_receipts(filters)
    # All charges: a receipt's charge can post after the end of the period.
    all_transactions = repository.list_bank_transactions(BankTransactionFilter())
    differences = charge_differences(all_receipts, all_transactions)
    receipts = [_with_difference(r, differences.get(r.receipt_id), filters) for r in receipts]
    transactions = [
        t
        for t in all_transactions
        if (filters.date_from is None or t.date >= filters.date_from)
        and (filters.date_to is None or t.date <= filters.date_to)
    ]
    records = [
        bank_record(t) for t in unexplained_charges(transactions, {r.receipt_id for r in all_receipts})
    ]
    if filters.filters_items:
        records = [r for r in records if filters.matches_item(r.items[0])]
    combined = receipts + records
    return sorted(combined, key=lambda r: (r.purchased_at, r.created_at), reverse=True)
