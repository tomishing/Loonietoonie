from typing import Protocol

from app.storage.models import Category, Receipt, ReceiptDraft, ReceiptFilter


class StorageError(Exception):
    """The user's storage (Google Sheets) could not be read or written."""


class Repository(Protocol):
    """The only way routes and services read or write user data.

    `SheetsRepository` is the current implementation. A local cache or a
    `PostgresRepository` can be added later without changing business logic.
    """

    def list_categories(self) -> list[Category]:
        """Active (not deleted) categories, in sheet order."""
        ...

    def list_receipts(self, filters: ReceiptFilter) -> list[Receipt]:
        """Active receipts, newest purchase date first, each with its active items."""
        ...

    def get_receipt(self, receipt_id: str) -> Receipt | None: ...

    def create_receipt(self, draft: ReceiptDraft) -> Receipt:
        """Save a receipt and its items. The repository generates the UUIDs and timestamps."""
        ...

    def update_receipt(self, receipt_id: str, draft: ReceiptDraft) -> Receipt | None:
        """Replace a receipt's fields and items.

        Items whose `item_id` matches are updated, items without an ID are added,
        and existing items missing from the draft are soft-deleted.
        Returns None if the receipt does not exist.
        """
        ...

    def delete_receipt(self, receipt_id: str) -> bool:
        """Soft-delete a receipt and its items. Returns False if it does not exist."""
        ...
