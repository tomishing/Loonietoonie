"""Spreadsheet layout: tab names, columns, and the rows a new spreadsheet starts with.

Keep this in sync with specification.md §4. Adding a tab or a column here is
enough: existing spreadsheets are migrated on the next sign-in (see setup.py).
"""

from datetime import UTC, datetime

SPREADSHEET_NAME = "LoonieToonie"
FOLDER_NAME = "LoonieToonie"

# Drive appProperties marker, so files are found even if the user renames them.
APP_PROPERTY_KEY = "loonietoonie"
SPREADSHEET_MARKER = "spreadsheet"
FOLDER_MARKER = "root-folder"

SPREADSHEET_MIME_TYPE = "application/vnd.google-apps.spreadsheet"
FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"

TRUE = "TRUE"
FALSE = "FALSE"

TABS: dict[str, tuple[str, ...]] = {
    "receipts": (
        "receipt_id",
        "store",
        "purchased_at",
        "total",
        "currency",
        "image_link",
        "created_at",
        "updated_at",
        "deleted",
    ),
    "items": ("item_id", "receipt_id", "name", "quantity", "price", "category", "updated_at", "deleted"),
    "categories": ("name", "icon", "updated_at", "deleted"),
    "budgets": ("month", "category", "amount", "updated_at", "deleted"),
    "settings": ("key", "value", "updated_at"),
    "ocr_labels": ("receipt_id", "field", "model_value", "corrected_value"),
}

DEFAULT_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("Groceries", "🛒"),
    ("Dining Out", "🍜"),
    ("Household", "🧻"),
    ("Transportation", "🚗"),
    ("Utilities", "💡"),
    ("Housing", "🏠"),
    ("Health", "💊"),
    ("Personal Care", "🧴"),
    ("Clothing", "👕"),
    ("Entertainment", "🎮"),
    ("Kids", "🧸"),
    ("Pets", "🐾"),
    ("Tax & Fees", "🧾"),
    ("Other", "📦"),
)

TAX_CATEGORY = "Tax & Fees"
# Categories the app relies on. Added to existing spreadsheets on sign-in unless a row
# with that name already exists (a row the user soft-deleted counts, so it isn't re-added).
REQUIRED_CATEGORIES: tuple[tuple[str, str], ...] = ((TAX_CATEGORY, "🧾"),)


def utc_now_iso() -> str:
    """Current time as ISO 8601 UTC, e.g. 2026-10-13T09:30:00Z."""
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def tab_range(tab: str, cell: str | None = None) -> str:
    """A1 range for a whole tab, or for one cell in it (quoted, so any tab name works)."""
    quoted = "'" + tab.replace("'", "''") + "'"
    return f"{quoted}!{cell}" if cell else quoted
