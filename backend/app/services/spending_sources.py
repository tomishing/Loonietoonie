"""All spending in one list: receipts plus bank and card charges that no receipt explains.

Patterns, Ask, and CSV export work on receipts. A charge matched to a receipt is left out (the
receipt and its items already count); every other spending charge becomes a one-item record
with receipt_id "bank:<transaction_id>", the merchant as store, and its category. Transfers and
income never count.
"""

from decimal import Decimal

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


def spending_records(repository: Repository, filters: ReceiptFilter | None = None) -> list[Receipt]:
    """Receipts and unexplained bank/card spending, newest first, with the same filters as receipts.

    With a category or subcategory filter, a charge is included when its category matches.
    """
    filters = filters or ReceiptFilter()
    all_receipts = repository.list_receipts(ReceiptFilter())
    receipts = all_receipts if filters == ReceiptFilter() else repository.list_receipts(filters)
    transactions = repository.list_bank_transactions(
        BankTransactionFilter(date_from=filters.date_from, date_to=filters.date_to)
    )
    records = [
        bank_record(t) for t in unexplained_charges(transactions, {r.receipt_id for r in all_receipts})
    ]
    if filters.filters_items:
        records = [r for r in records if filters.matches_item(r.items[0])]
    combined = receipts + records
    return sorted(combined, key=lambda r: (r.purchased_at, r.created_at), reverse=True)
