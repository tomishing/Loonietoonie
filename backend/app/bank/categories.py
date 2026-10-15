"""Map Plaid's personal finance categories to LoonieToonie's categories and subcategories.

Plaid labels each transaction with a primary and a detailed category (e.g. FOOD_AND_DRINK /
FOOD_AND_DRINK_COFFEE). Transfers, credit-card payments, and income are not spending, so they
get their own `kind` and don't count towards spending patterns.
"""

from dataclasses import dataclass

from app.storage.models import BankTransactionKind, Category

OTHER = "Other"


@dataclass(frozen=True)
class Mapped:
    kind: BankTransactionKind
    category: str = ""
    subcategory: str = ""


# Detailed Plaid category → (category, subcategory) using the default category names.
DETAILED: dict[str, tuple[str, str]] = {
    "FOOD_AND_DRINK_GROCERIES": ("Groceries", ""),
    "FOOD_AND_DRINK_BEER_WINE_AND_LIQUOR": ("Groceries", "Drinks"),
    "FOOD_AND_DRINK_RESTAURANT": ("Dining Out", "Restaurants"),
    "FOOD_AND_DRINK_FAST_FOOD": ("Dining Out", "Fast Food"),
    "FOOD_AND_DRINK_COFFEE": ("Dining Out", "Coffee & Cafés"),
    "FOOD_AND_DRINK_VENDING_MACHINES": ("Dining Out", "Fast Food"),
    "GENERAL_MERCHANDISE_CLOTHING_AND_ACCESSORIES": ("Clothing", ""),
    "GENERAL_MERCHANDISE_PET_SUPPLIES": ("Pets", "Pet Supplies"),
    "GENERAL_MERCHANDISE_BOOKSTORES_AND_NEWSSTANDS": ("Entertainment", "Books & Music"),
    "GENERAL_MERCHANDISE_CONVENIENCE_STORES": ("Groceries", ""),
    "GENERAL_MERCHANDISE_SUPERSTORES": ("Household", ""),
    "GENERAL_MERCHANDISE_DISCOUNT_STORES": ("Household", ""),
    "GENERAL_MERCHANDISE_DEPARTMENT_STORES": ("Household", ""),
    "HOME_IMPROVEMENT_FURNITURE": ("Household", "Furniture & Decor"),
    "HOME_IMPROVEMENT_HARDWARE": ("Household", "Tools & Hardware"),
    "HOME_IMPROVEMENT_REPAIR_AND_MAINTENANCE": ("Housing", "Repairs"),
    "HOME_IMPROVEMENT_SECURITY": ("Housing", "Repairs"),
    "TRANSPORTATION_GAS": ("Transportation", "Fuel"),
    "TRANSPORTATION_PUBLIC_TRANSIT": ("Transportation", "Public Transit"),
    "TRANSPORTATION_TAXIS_AND_RIDE_SHARES": ("Transportation", "Taxi & Rideshare"),
    "TRANSPORTATION_PARKING": ("Transportation", "Parking"),
    "TRANSPORTATION_TOLLS": ("Transportation", "Parking"),
    "GENERAL_SERVICES_AUTOMOTIVE": ("Transportation", "Car Maintenance"),
    "RENT_AND_UTILITIES_RENT": ("Housing", "Rent & Mortgage"),
    "LOAN_PAYMENTS_MORTGAGE_PAYMENT": ("Housing", "Rent & Mortgage"),
    "GENERAL_SERVICES_INSURANCE": ("Housing", "Insurance"),
    "RENT_AND_UTILITIES_GAS_AND_ELECTRICITY": ("Utilities", "Electricity"),
    "RENT_AND_UTILITIES_WATER": ("Utilities", "Water"),
    "RENT_AND_UTILITIES_SEWAGE_AND_WASTE_MANAGEMENT": ("Utilities", "Water"),
    "RENT_AND_UTILITIES_INTERNET_AND_CABLE": ("Utilities", "Internet"),
    "RENT_AND_UTILITIES_TELEPHONE": ("Utilities", "Phone"),
    "MEDICAL_PHARMACIES_AND_SUPPLEMENTS": ("Health", "Pharmacy"),
    "MEDICAL_DENTAL_CARE": ("Health", "Medical & Dental"),
    "MEDICAL_PRIMARY_CARE": ("Health", "Medical & Dental"),
    "MEDICAL_EYE_CARE": ("Health", "Vision"),
    "PERSONAL_CARE_HAIR_AND_BEAUTY": ("Personal Care", "Hair"),
    "ENTERTAINMENT_TV_AND_MOVIES": ("Entertainment", "Movies & Shows"),
    "ENTERTAINMENT_VIDEO_GAMES": ("Entertainment", "Games"),
    "ENTERTAINMENT_MUSIC_AND_AUDIO": ("Entertainment", "Books & Music"),
    "ENTERTAINMENT_SPORTING_EVENTS_AMUSEMENT_PARKS_AND_MUSEUMS": ("Entertainment", "Events"),
    "GENERAL_SERVICES_CHILDCARE": ("Kids", "Activities"),
    "GENERAL_SERVICES_EDUCATION": ("Kids", "School Supplies"),
}

# Fallback by primary Plaid category.
PRIMARY: dict[str, tuple[str, str]] = {
    "FOOD_AND_DRINK": ("Dining Out", ""),
    "GENERAL_MERCHANDISE": (OTHER, ""),
    "HOME_IMPROVEMENT": ("Household", ""),
    "TRANSPORTATION": ("Transportation", ""),
    "TRAVEL": ("Transportation", ""),
    "RENT_AND_UTILITIES": ("Utilities", ""),
    "MEDICAL": ("Health", ""),
    "PERSONAL_CARE": ("Personal Care", ""),
    "ENTERTAINMENT": ("Entertainment", ""),
    "GENERAL_SERVICES": (OTHER, ""),
    "GOVERNMENT_AND_NON_PROFIT": (OTHER, ""),
    "BANK_FEES": ("Tax & Fees", ""),
}

# Not spending: money moving between the user's own accounts, debt payments, and income.
TRANSFER_PRIMARIES = frozenset({"TRANSFER_IN", "TRANSFER_OUT", "LOAN_PAYMENTS"})
INCOME_PRIMARIES = frozenset({"INCOME"})


def map_category(primary: str, detailed: str, categories: list[Category]) -> Mapped:
    """Plaid's category → kind, plus the user's own category and subcategory names.

    A mapped name that the user's list doesn't have falls back to Other (category) or to no
    subcategory. Names are matched case-insensitively and returned with the user's spelling.
    """
    primary, detailed = primary.strip().upper(), detailed.strip().upper()
    # The detailed code starts with its primary (LOAN_PAYMENTS_CREDIT_CARD_PAYMENT), so a missing
    # or unexpected primary can be recovered from it; transfers must never count as spending.
    known = TRANSFER_PRIMARIES | INCOME_PRIMARIES | PRIMARY.keys()
    if primary not in known:
        primary = next(
            (p for p in sorted(known, key=len, reverse=True) if detailed.startswith(p + "_")), primary
        )
    target = DETAILED.get(detailed)
    if target is None:
        if primary in INCOME_PRIMARIES:
            return Mapped(kind="income")
        if primary in TRANSFER_PRIMARIES:
            return Mapped(kind="transfer")
        target = PRIMARY.get(primary, (OTHER, ""))

    by_name = {c.name.casefold(): c for c in categories}
    category = by_name.get(target[0].casefold()) or by_name.get(OTHER.casefold())
    if category is None:
        return Mapped(kind="spending", category=categories[0].name if categories else OTHER)
    subcategory = next((s for s in category.subcategories if s.casefold() == target[1].casefold()), "")
    return Mapped(kind="spending", category=category.name, subcategory=subcategory)
