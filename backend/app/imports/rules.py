"""Money that isn't spending, recognized from a statement row's text (English and French).

Plaid told card payments, transfers, and income apart; statement files don't, so these rules
run before Claude is asked about a merchant (services/imports.py).
"""

import re
import unicodedata
from decimal import Decimal

from app.storage.models import BankTransactionKind

# Paying the card off, as the card's statement shows it.
_CARD_PAYMENTS = (
    "payment thank you", "paiement merci", "payment received", "paiement recu", "autopay",
    "automatic payment", "paiement automatique", "pre authorized payment", "pymt received",
)  # fmt: skip
# On a bank account: paying a credit card from it (CIBC: "INTERNET BILL PAY 000000 CIBC VISA").
_CARD_NAMES = ("visa", "mastercard", "master card", "amex", "american express")
_PAYMENT_WORDS = ("pay", "payment", "pmt", "paiement", "bill pay")
_TRANSFERS = (
    "transfer", "e transfer", "etransfer", "virement", "tfr", "xfer", "interac e", "transfert",
)  # fmt: skip
# Money coming into a bank account.
_INCOME = (
    "payroll", "pay", "salary", "paie", "salaire", "direct dep", "direct deposit", "depot direct",
    "interest", "interet", "gst", "ccb", "cpp", "ei", "pension", "dividend",
)  # fmt: skip


def _words(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return " " + " ".join(re.split(r"[^a-z]+", plain)).strip() + " "


def _has(text: str, phrases: tuple[str, ...]) -> bool:
    return any(f" {phrase} " in text for phrase in phrases)


def keyword_kind(description: str, amount: Decimal, account_type: str) -> BankTransactionKind | None:
    """`transfer` or `income` when the text says so; None when it may be spending (amount > 0 is
    money out, as in bank_transactions)."""
    text = _words(description)
    if account_type == "credit":
        if amount < 0 and (_has(text, _CARD_PAYMENTS) or _has(text, _TRANSFERS)):
            return "transfer"
        return None
    if _has(text, _CARD_NAMES) and _has(text, _PAYMENT_WORDS):
        return "transfer"
    if amount < 0 and _has(text, _INCOME):
        return "income"
    if _has(text, _TRANSFERS):
        return "transfer"
    return None
