from typing import Protocol

from app.storage.models import (
    BankAccount,
    BankChanges,
    BankTransaction,
    BankTransactionFilter,
    Category,
    MerchantRule,
    Receipt,
    ReceiptDraft,
    ReceiptFilter,
)


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

    def attach_receipt_image(self, receipt_id: str, data: bytes, media_type: str) -> Receipt | None:
        """Store a receipt photo (Drive: LoonieToonie/receipts/) and set the receipt's image_link.

        Returns None if the receipt does not exist.
        """
        ...

    def delete_receipt(self, receipt_id: str) -> bool:
        """Soft-delete a receipt and its items. Returns False if it does not exist."""
        ...

    # --- bank and card linking (phase 10) -------------------------------------

    def list_bank_accounts(self) -> list[BankAccount]:
        """Active linked accounts, in sheet order."""
        ...

    def save_bank_accounts(self, accounts: list[BankAccount]) -> None:
        """Add or update accounts by account_id (a re-linked account is undeleted)."""
        ...

    def remove_bank_accounts(self, item_id: str) -> None:
        """Soft-delete the accounts of an unlinked item. Their transactions are kept."""
        ...

    def apply_bank_changes(self, upserts: list[BankTransaction], removed_ids: list[str]) -> BankChanges:
        """Add new transactions, update known ones, and soft-delete removed ones, deduplicated by
        transaction_id. On update, the bank's fields change but kind, category, subcategory, and
        receipt_id (which the user may have edited) are kept.
        """
        ...

    def list_bank_transactions(self, filters: BankTransactionFilter) -> list[BankTransaction]:
        """Active transactions, newest first."""
        ...

    def set_bank_receipt_ids(self, updates: dict[str, str]) -> None:
        """Set receipt_id on transactions {transaction_id: receipt_id} (receipt matching)."""
        ...

    def remove_bank_transactions(self, account_ids: list[str]) -> int:
        """Soft-delete all transactions of these accounts (unlink with "remove transactions")."""
        ...

    def update_bank_fields(self, updates: dict[str, dict[str, str]]) -> None:
        """Change user fields (kind, category, subcategory, receipt_id) of transactions
        {transaction_id: {column: value}}, all in one write."""
        ...

    def list_merchant_rules(self) -> list[MerchantRule]:
        """Active remembered categories, one per merchant."""
        ...

    def save_merchant_rule(self, rule: MerchantRule) -> None:
        """Add or replace the remembered category for rule.merchant."""
        ...
