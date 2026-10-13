"""Receipt rules that need the user's data: categories must exist, item IDs must belong to the receipt."""

from datetime import date, timedelta

from app.storage.models import Receipt, ReceiptDraft, ReceiptFilter
from app.storage.repository import Repository

# Allows for time zones ahead of the server.
MAX_DAYS_IN_FUTURE = 1


class ReceiptValidationError(Exception):
    def __init__(self, errors: list[dict[str, object]]) -> None:
        super().__init__("Invalid receipt")
        self.errors = errors


class ReceiptNotFoundError(Exception):
    pass


def _error(loc: list[str | int], msg: str) -> dict[str, object]:
    return {"loc": ["body", *loc], "msg": msg, "type": "value_error"}


def _validate(repository: Repository, draft: ReceiptDraft, existing: Receipt | None) -> ReceiptDraft:
    """Check the draft against the user's data. Category names are matched case-insensitively."""
    errors: list[dict[str, object]] = []

    if draft.purchased_at > date.today() + timedelta(days=MAX_DAYS_IN_FUTURE):
        errors.append(_error(["purchased_at"], "The purchase date can't be in the future"))

    categories = {category.name.casefold(): category.name for category in repository.list_categories()}
    existing_ids = {item.item_id for item in existing.items} if existing else set()
    seen_ids: set[str] = set()
    items = []
    for index, item in enumerate(draft.items):
        canonical = categories.get(item.category.casefold())
        if canonical is None:
            errors.append(_error(["items", index, "category"], f"Unknown category '{item.category}'"))
        if item.item_id is not None:
            if item.item_id not in existing_ids:
                errors.append(_error(["items", index, "item_id"], "This item doesn't belong to the receipt"))
            elif item.item_id in seen_ids:
                errors.append(_error(["items", index, "item_id"], "This item appears twice"))
            seen_ids.add(item.item_id)
        items.append(item.model_copy(update={"category": canonical or item.category}))

    if errors:
        raise ReceiptValidationError(errors)
    return draft.model_copy(update={"items": items})


def list_receipts(repository: Repository, filters: ReceiptFilter) -> list[Receipt]:
    return repository.list_receipts(filters)


def get_receipt(repository: Repository, receipt_id: str) -> Receipt:
    receipt = repository.get_receipt(receipt_id)
    if receipt is None:
        raise ReceiptNotFoundError(receipt_id)
    return receipt


def create_receipt(repository: Repository, draft: ReceiptDraft) -> Receipt:
    return repository.create_receipt(_validate(repository, draft, existing=None))


def update_receipt(repository: Repository, receipt_id: str, draft: ReceiptDraft) -> Receipt:
    existing = get_receipt(repository, receipt_id)
    updated = repository.update_receipt(receipt_id, _validate(repository, draft, existing))
    if updated is None:
        raise ReceiptNotFoundError(receipt_id)
    return updated


def delete_receipt(repository: Repository, receipt_id: str) -> None:
    if not repository.delete_receipt(receipt_id):
        raise ReceiptNotFoundError(receipt_id)
