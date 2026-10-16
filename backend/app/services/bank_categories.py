"""Changing a bank charge's category, and remembering it for every charge from the same merchant.

Plaid's category is a guess (often from its own merchant guess). When the user picks a category
for a charge with "all charges from this merchant", the choice is saved in the merchant_categories
tab, applied to the merchant's past charges now, and to new charges at import (services/bank.py).
"""

from app.services.names import merchant_key
from app.storage.models import BankTransactionFilter, BankTransactionKind, MerchantRule
from app.storage.repository import Repository


class CategoryChoiceError(Exception):
    """The chosen category or subcategory doesn't exist."""


class TransactionNotFoundError(Exception):
    pass


def set_category(
    repository: Repository,
    transaction_id: str,
    kind: BankTransactionKind,
    category: str,
    subcategory: str,
    apply_to_merchant: bool,
) -> int:
    """Set kind/category/subcategory on a charge (and, with apply_to_merchant, on every charge with
    the same merchant key). Category names are matched case-insensitively and saved with the
    sheet's spelling; non-spending kinds have no category. Returns the number of changed charges.
    """
    transactions = repository.list_bank_transactions(BankTransactionFilter())
    target = next((t for t in transactions if t.transaction_id == transaction_id), None)
    if target is None:
        raise TransactionNotFoundError(transaction_id)

    if kind == "spending":
        listed = {c.name.casefold(): c for c in repository.list_categories()}
        chosen = listed.get(category.strip().casefold())
        if chosen is None:
            raise CategoryChoiceError(f"Unknown category '{category}'")
        category = chosen.name
        if subcategory.strip():
            match = next(
                (s for s in chosen.subcategories if s.casefold() == subcategory.strip().casefold()), None
            )
            if match is None:
                raise CategoryChoiceError(f"'{subcategory}' isn't a subcategory of {chosen.name}")
            subcategory = match
        else:
            subcategory = ""
    else:
        category, subcategory = "", ""

    fields = {"kind": kind, "category": category, "subcategory": subcategory}
    key = merchant_key(target.merchant or target.description)
    changed = (
        [t for t in transactions if merchant_key(t.merchant or t.description) == key]
        if apply_to_merchant and key
        else [target]
    )
    repository.update_bank_fields({t.transaction_id: fields for t in changed})
    if apply_to_merchant and key:
        repository.save_merchant_rule(
            MerchantRule(merchant=key, kind=kind, category=category, subcategory=subcategory)
        )
    return len(changed)
