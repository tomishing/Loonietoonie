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
    "subcategories": ("category", "name", "description", "updated_at", "deleted"),
    # Linked bank and card accounts (no balances, no full numbers: just the last 4 digits).
    "bank_accounts": (
        "account_id",
        "item_id",
        "institution",
        "name",
        "mask",
        "type",
        "subtype",
        "currency",
        "updated_at",
        "deleted",
    ),
    # Imported from Plaid. amount > 0 is money spent, < 0 a refund or money in. kind is
    # spending | income | transfer; only spending counts towards spending patterns.
    "bank_transactions": (
        "transaction_id",
        "account_id",
        "date",
        "merchant",
        "description",
        "amount",
        "currency",
        "kind",
        "category",
        "subcategory",
        "plaid_category",
        "pending",
        "receipt_id",
        "updated_at",
        "deleted",
    ),
    # Categories the user chose for a merchant's bank charges (merchant = services/names.merchant_key).
    "merchant_categories": ("merchant", "kind", "category", "subcategory", "updated_at", "deleted"),
    # Merchant names learned from matched receipts (merchant = services/names.bank_key).
    "merchant_names": ("merchant", "name", "updated_at", "deleted"),
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
    ("Travel", "✈️"),
    ("Hobbies", "🎨"),
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
    "Travel": (
        "Flights",
        "Lodging",
        "Car Rental",
        "Trains & Buses",
        "Tours & Activities",
        "Travel Insurance & Fees",
    ),
    "Hobbies": (
        "Arts & Crafts",
        "Sports & Fitness",
        "Outdoors & Camping",
        "Music & Instruments",
        "Gardening & Plants",
        "Classes & Clubs",
    ),
    "Tax & Fees": ("Sales Tax", "Deposits & Eco Fees", "Bag Fees", "Tips & Service"),
}

# What each default subcategory covers. Claude reads these when it picks subcategories, and they
# are written into the description column (new tabs, and blank descriptions of default rows).
SUBCATEGORY_DESCRIPTIONS: dict[tuple[str, str], str] = {
    ("Groceries", "Fresh Fruit & Vegetables"): "Fresh fruit and vegetables, herbs, mushrooms, salad kits",
    ("Groceries", "Meat & Seafood"): "Meat, poultry, fish, seafood, sausages, ham, bacon, deli meats",
    (
        "Groceries",
        "Tofu & Plant Protein",
    ): "Tofu (fresh, fried, dried), tempeh, seitan, natto, plant-based meat",
    ("Groceries", "Dairy & Eggs"): "Milk, cream, butter, cheese, yogurt, eggs, plant-based milk",
    ("Groceries", "Bakery"): "Fresh bread, buns, bagels, pastries, cakes",
    ("Groceries", "Pantry"): (
        "Shelf-stable cooking basics: oils, vinegar, sauces, condiments, spices, sugar, baking "
        "supplies, canned and jarred food"
    ),
    ("Groceries", "Rice, Grains & Cereal"): "Rice, pasta, noodles, flour, oats, breakfast cereal",
    ("Groceries", "Frozen"): "Frozen meals, frozen vegetables and fruit, ice cream, frozen snacks",
    ("Groceries", "Snacks & Sweets"): "Chips, crackers, cookies, candy, chocolate, snack nuts",
    ("Groceries", "Drinks"): "Water, juice, soft drinks, coffee, tea, beer, wine",
    ("Dining Out", "Restaurants"): "Sit-down meals and restaurant takeout",
    ("Dining Out", "Fast Food"): "Quick-service chains, food courts, convenience-store hot food",
    ("Dining Out", "Coffee & Cafés"): "Coffee shops, café drinks and pastries",
    ("Dining Out", "Delivery"): "Meals ordered through delivery apps, delivery fees",
    ("Dining Out", "Bars"): "Bars, pubs, drinks out",
    ("Household", "Cleaning"): "Laundry detergent, dish soap, cleaners, sponges, trash bags",
    ("Household", "Paper Goods"): "Toilet paper, paper towels, tissues, napkins",
    ("Household", "Kitchen"): "Cookware, utensils, food storage, foil and plastic wrap",
    ("Household", "Furniture & Decor"): "Furniture, bedding, towels, decorations",
    ("Household", "Tools & Hardware"): "Tools, light bulbs, batteries, hardware, garden supplies",
    ("Transportation", "Fuel"): "Gasoline, diesel, EV charging",
    ("Transportation", "Public Transit"): "Bus, subway, and train fares and passes",
    ("Transportation", "Taxi & Rideshare"): "Taxis, Uber, Lyft",
    ("Transportation", "Parking"): "Parking fees and permits, tolls",
    ("Transportation", "Car Maintenance"): "Repairs, oil changes, tires, car wash, car parts",
    ("Utilities", "Electricity"): "Electricity bills",
    ("Utilities", "Gas & Heating"): "Natural gas, heating oil, propane",
    ("Utilities", "Water"): "Water and sewer bills",
    ("Utilities", "Internet"): "Home internet",
    ("Utilities", "Phone"): "Mobile and home phone plans",
    ("Housing", "Rent & Mortgage"): "Rent, mortgage payments, condo fees",
    ("Housing", "Repairs"): "Home repairs and maintenance, contractors",
    ("Housing", "Insurance"): "Home or tenant insurance",
    ("Health", "Pharmacy"): "Prescriptions, over-the-counter medicine, first aid",
    ("Health", "Vitamins & Supplements"): "Vitamins, supplements, protein powder",
    ("Health", "Medical & Dental"): "Doctor, dentist, physiotherapy, clinic fees",
    ("Health", "Vision"): "Glasses, contact lenses, eye exams",
    ("Personal Care", "Hair"): "Haircuts, shampoo, conditioner, styling products",
    ("Personal Care", "Skin Care"): "Moisturizer, sunscreen, face wash",
    ("Personal Care", "Cosmetics"): "Makeup, nail products",
    ("Personal Care", "Hygiene"): "Toothpaste, toothbrushes, deodorant, soap, razors, menstrual products",
    ("Clothing", "Clothes"): "Clothing, underwear, socks",
    ("Clothing", "Shoes"): "Shoes, boots, sandals",
    ("Clothing", "Accessories"): "Bags, hats, belts, jewellery, watches",
    ("Entertainment", "Movies & Shows"): "Cinema tickets, movie rentals and purchases",
    ("Entertainment", "Games"): "Video games, board games",
    ("Entertainment", "Books & Music"): "Books, magazines, music",
    ("Entertainment", "Events"): "Concerts, sports games, museums, attractions",
    ("Entertainment", "Subscriptions"): "Streaming and other entertainment subscriptions",
    ("Kids", "Toys"): "Toys and games for children",
    ("Kids", "School Supplies"): "School supplies and school fees",
    ("Kids", "Baby Care"): "Diapers, wipes, formula, baby food",
    ("Kids", "Activities"): "Lessons, camps, children's activities",
    ("Pets", "Pet Food"): "Pet food and treats",
    ("Pets", "Vet"): "Vet visits, pet medicine",
    ("Pets", "Pet Supplies"): "Litter, toys, grooming, accessories",
    ("Tax & Fees", "Sales Tax"): (
        "GST, HST, PST, QST/TVQ, US sales tax, Japanese consumption tax added on top of prices"
    ),
    ("Tax & Fees", "Deposits & Eco Fees"): "Bottle deposits, environmental and recycling fees",
    ("Tax & Fees", "Bag Fees"): "Shopping bag charges",
    ("Tax & Fees", "Tips & Service"): "Tips, gratuities, service charges",
    ("Travel", "Flights"): "Airfare, seat selection, checked and carry-on bag fees",
    ("Travel", "Lodging"): "Hotels, motels, hostels, Airbnb and vacation rentals",
    ("Travel", "Car Rental"): "Rental cars and their insurance and fuel charges",
    ("Travel", "Trains & Buses"): "Intercity trains, coaches, and ferries on a trip (not daily transit)",
    ("Travel", "Tours & Activities"): "Tours, attractions, tickets, and activities while travelling",
    (
        "Travel",
        "Travel Insurance & Fees",
    ): "Travel insurance, visas, passports, airport and travel agency fees",
    # Hobbies are things you do or make; Entertainment is things you watch, read, or listen to.
    ("Hobbies", "Arts & Crafts"): "Art supplies, craft materials, sewing, knitting, model kits",
    ("Hobbies", "Sports & Fitness"): "Gym memberships, sports equipment, club and league fees",
    ("Hobbies", "Outdoors & Camping"): "Camping, hiking, fishing, and cycling gear, park passes",
    ("Hobbies", "Music & Instruments"): "Musical instruments, sheet music, music gear and repairs",
    ("Hobbies", "Gardening & Plants"): "Plants, seeds, soil, and garden tools for the hobby",
    ("Hobbies", "Classes & Clubs"): "Adult classes, workshops, hobby clubs and memberships",
}

# Defaults added after the subcategories tab first shipped. Appended to existing spreadsheets on
# migration unless a row with that category and name exists (a soft-deleted row counts).
ADDED_SUBCATEGORIES: tuple[tuple[str, str], ...] = (
    ("Groceries", "Tofu & Plant Protein"),
    ("Groceries", "Rice, Grains & Cereal"),
    *(("Travel", name) for name in DEFAULT_SUBCATEGORIES["Travel"]),
    *(("Hobbies", name) for name in DEFAULT_SUBCATEGORIES["Hobbies"]),
)
# Defaults renamed since they first shipped: (category, old name) → new name. Applied on migration to
# an active row with the old name when no row with the new name exists. Saved items keep the old name.
RENAMED_SUBCATEGORIES: dict[tuple[str, str], str] = {
    ("Groceries", "Produce"): "Fresh Fruit & Vegetables",
}

TAX_CATEGORY = "Tax & Fees"
# Categories the app relies on. Added to existing spreadsheets on sign-in unless a row
# with that name already exists (a row the user soft-deleted counts, so it isn't re-added).
REQUIRED_CATEGORIES: tuple[tuple[str, str], ...] = ((TAX_CATEGORY, "🧾"),)
# Default categories added after the first release, appended to existing spreadsheets the same way.
ADDED_CATEGORIES: tuple[tuple[str, str], ...] = (("Travel", "✈️"), ("Hobbies", "🎨"))


def utc_now_iso() -> str:
    """Current time as ISO 8601 UTC, e.g. 2026-10-13T09:30:00Z."""
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def tab_range(tab: str, cell: str | None = None) -> str:
    """A1 range for a whole tab, or for one cell in it (quoted, so any tab name works)."""
    quoted = "'" + tab.replace("'", "''") + "'"
    return f"{quoted}!{cell}" if cell else quoted
