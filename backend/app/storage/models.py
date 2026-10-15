from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field, StringConstraints, field_validator

from app.config import DEFAULT_CURRENCY

MAX_MONEY_DECIMALS = 3


def _max_decimals(value: Decimal) -> Decimal:
    exponent = value.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -MAX_MONEY_DECIMALS:
        raise ValueError(f"Use at most {MAX_MONEY_DECIMALS} decimal places")
    return value


Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Money = Annotated[Decimal, Field(allow_inf_nan=False), AfterValidator(_max_decimals)]


class Category(BaseModel):
    name: str
    icon: str
    # Finer groups within the category, from the subcategories tab, in sheet order.
    subcategories: list[str] = Field(default_factory=list)
    # What a subcategory covers (subcategory name → description); only non-blank ones.
    descriptions: dict[str, str] = Field(default_factory=dict)


def _blank_to_none(value: str | None) -> str | None:
    return value or None


OptionalText = Annotated[
    str | None,
    StringConstraints(strip_whitespace=True, max_length=200),
    AfterValidator(_blank_to_none),
]


class ItemDraft(BaseModel):
    """An item as entered by the user. `item_id` is set only when editing an existing item."""

    item_id: str | None = None
    name: Text
    quantity: Annotated[Money, Field(gt=0, le=100_000)] = Decimal(1)
    # Unit price. Negative prices are allowed for discount lines.
    price: Annotated[Money, Field(ge=-10_000_000, le=10_000_000)]
    category: Text
    # Optional; must belong to `category` when given. Blank means none.
    subcategory: OptionalText = None


class ReceiptDraft(BaseModel):
    store: Text
    purchased_at: date
    total: Annotated[Money, Field(ge=0, le=100_000_000)]
    currency: str = DEFAULT_CURRENCY
    items: list[ItemDraft] = Field(min_length=1, max_length=200)

    @field_validator("currency")
    @classmethod
    def _currency_code(cls, value: str) -> str:
        code = value.strip().upper()
        if len(code) != 3 or not code.isascii() or not code.isalpha():
            raise ValueError("Use a 3-letter currency code such as CAD, USD, or JPY")
        return code


class Item(BaseModel):
    item_id: str
    name: str
    quantity: Decimal
    price: Decimal
    category: str
    subcategory: str = ""


class Receipt(BaseModel):
    receipt_id: str
    store: str
    purchased_at: date
    total: Decimal
    currency: str
    image_link: str
    created_at: str
    updated_at: str
    items: list[Item]


class ReceiptFilter(BaseModel):
    date_from: date | None = None
    date_to: date | None = None
    category: str | None = None
    subcategory: str | None = None

    @property
    def filters_items(self) -> bool:
        return bool(self.category or self.subcategory)

    def matches_item(self, item: Item) -> bool:
        """Whether an item passes the category / subcategory filters (case-insensitive)."""
        if self.category and item.category.casefold() != self.category.strip().casefold():
            return False
        if self.subcategory and item.subcategory.casefold() != self.subcategory.strip().casefold():
            return False
        return True


BankTransactionKind = Literal["spending", "income", "transfer"]


class BankAccount(BaseModel):
    account_id: str
    item_id: str
    institution: str
    name: str
    mask: str  # last 4 digits, or ""
    type: str  # depository | credit | loan | investment | other
    subtype: str
    currency: str


class BankTransaction(BaseModel):
    transaction_id: str
    account_id: str
    date: date
    merchant: str
    description: str
    # > 0 money spent, < 0 refund or money in.
    amount: Decimal
    currency: str
    kind: BankTransactionKind
    category: str
    subcategory: str
    plaid_category: str
    pending: bool
    # The receipt that explains this charge (receipt matching, phase 10b). "" for none.
    receipt_id: str = ""


class BankTransactionFilter(BaseModel):
    date_from: date | None = None
    date_to: date | None = None
    account_id: str | None = None
    category: str | None = None


class BankChanges(BaseModel):
    added: int = 0
    modified: int = 0
    removed: int = 0
