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
    "items": (
        "item_id",
        "receipt_id",
        "name",
        "quantity",
        "price",
        "category",
        "subcategory",
        "updated_at",
        "deleted",
    ),
    "categories": ("name", "icon", "updated_at", "deleted"),
    "subcategories": ("category", "name", "updated_at", "deleted"),
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

# Written when the subcategories tab is created (new spreadsheets, and existing ones on migration).
DEFAULT_SUBCATEGORIES: dict[str, tuple[str, ...]] = {
    "Groceries": (
        "Fresh Fruit & Vegetables",
        "Meat & Seafood",
        "Tofu & Plant Protein",
        "Dairy & Eggs",
        "Bakery",
        "Pantry",
        "Rice, Grains & Cereal",
        "Frozen",
        "Snacks & Sweets",
        "Drinks",
    ),
    "Dining Out": ("Restaurants", "Fast Food", "Coffee & Cafés", "Delivery", "Bars"),
    "Household": ("Cleaning", "Paper Goods", "Kitchen", "Furniture & Decor", "Tools & Hardware"),
    "Transportation": ("Fuel", "Public Transit", "Taxi & Rideshare", "Parking", "Car Maintenance"),
    "Utilities": ("Electricity", "Gas & Heating", "Water", "Internet", "Phone"),
    "Housing": ("Rent & Mortgage", "Repairs", "Insurance"),
    "Health": ("Pharmacy", "Vitamins & Supplements", "Medical & Dental", "Vision"),
    "Personal Care": ("Hair", "Skin Care", "Cosmetics", "Hygiene"),
    "Clothing": ("Clothes", "Shoes", "Accessories"),
    "Entertainment": ("Movies & Shows", "Games", "Books & Music", "Events", "Subscriptions"),
    "Kids": ("Toys", "School Supplies", "Baby Care", "Activities"),
    "Pets": ("Pet Food", "Vet", "Pet Supplies"),
    "Tax & Fees": ("Sales Tax", "Deposits & Eco Fees", "Bag Fees", "Tips & Service"),
}

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
