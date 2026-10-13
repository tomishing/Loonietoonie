from datetime import date
from decimal import Decimal
from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field, StringConstraints, field_validator

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


class ItemDraft(BaseModel):
    """An item as entered by the user. `item_id` is set only when editing an existing item."""

    item_id: str | None = None
    name: Text
    quantity: Annotated[Money, Field(gt=0, le=100_000)] = Decimal(1)
    # Unit price. Negative prices are allowed for discount lines.
    price: Annotated[Money, Field(ge=-10_000_000, le=10_000_000)]
    category: Text


class ReceiptDraft(BaseModel):
    store: Text
    purchased_at: date
    total: Annotated[Money, Field(ge=0, le=100_000_000)]
    currency: str = "USD"
    items: list[ItemDraft] = Field(min_length=1, max_length=200)

    @field_validator("currency")
    @classmethod
    def _currency_code(cls, value: str) -> str:
        code = value.strip().upper()
        if len(code) != 3 or not code.isascii() or not code.isalpha():
            raise ValueError("Use a 3-letter currency code such as USD, CAD, or JPY")
        return code


class Item(BaseModel):
    item_id: str
    name: str
    quantity: Decimal
    price: Decimal
    category: str


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
