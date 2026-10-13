from typing import Protocol

from app.storage.models import Category


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
