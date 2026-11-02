"""Rules on a statement row's text (English and French): money that isn't spending, and well-known
Canadian public services and transit operators with a fixed category.

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


# Canadian public services and transit whose statement names are unambiguous, as phrases of the
# normalized text (lower-case, accent-free, digits and punctuation dropped: "407 ETR" is "etr").
# Default category and subcategory names; first match wins, so specific phrases come first.
# Names that also belong to other businesses ("Indigo", "OPUS", "exo") are left to Claude.
_KNOWN: tuple[tuple[tuple[str, ...], str, str], ...] = (
    # Public transit: fares, passes, and fare-card top-ups.
    (("translink", "compass vending", "compass account", "compass card", "bc transit", "presto",
      "metrolinx", "go transit", "up express", "ttc", "toronto transit", "oc transpo", "miway",
      "brampton transit", "york region transit", "yrt", "durham region transit",
      "grand river transit", "transit windsor", "stm", "societe de transport", "calgary transit",
      "edmonton transit", "winnipeg transit", "saskatoon transit", "regina transit",
      "halifax transit", "codiac transpo"), "Transportation", "Public Transit"),
    # Parking, and the 407 ETR toll highway.
    (("parking", "impark", "easypark", "easy park", "paybyphone", "pay by phone", "honk mobile",
      "honkmobile", "precise parklink", "green p", "etr"), "Transportation", "Parking"),
    # Public car insurance and driver services.
    (("icbc", "saaq", "manitoba public insurance", "sgi canada"), "Transportation", ""),
    # Intercity trains and ferries.
    (("via rail", "bc ferries", "marine atlantic"), "Travel", "Trains & Buses"),
    (("bc hydro", "hydro one", "hydro quebec", "toronto hydro", "hydro ottawa", "alectra",
      "london hydro", "elexicon", "enmax", "epcor", "fortisalberta", "fortis alberta",
      "nova scotia power", "ns power", "nb power", "saskpower", "sask power", "manitoba hydro",
      "newfoundland power", "maritime electric"), "Utilities", "Electricity"),
    (("fortisbc", "fortis bc", "enbridge", "saskenergy", "sask energy", "atco gas", "energir",
      "union gas", "centra gas"), "Utilities", "Gas & Heating"),
    # Mobile plans (before the carriers' general names below).
    (("fido", "koodo", "virgin plus", "virgin mobile", "freedom mobile", "public mobile", "chatr",
      "lucky mobile", "fizz", "rogers wireless", "bell mobility", "telus mobility"),
     "Utilities", "Phone"),
    (("teksavvy", "eastlink", "cogeco", "shaw", "distributel", "oxio", "novus"),
     "Utilities", "Internet"),
    # Carriers that sell both phone and internet.
    (("rogers", "telus", "bell canada", "bell aliant", "videotron", "sasktel", "tbaytel"),
     "Utilities", ""),
    # Government fees and taxes: passports, immigration, licences and registries, the CRA.
    (("service canada", "passport canada", "ircc", "immigration refugees", "canada revenue", "cra",
      "receiver general", "service bc", "serviceontario", "service ontario", "service nb",
      "service new brunswick", "service nova scotia", "access nova scotia", "alberta registries",
      "registry"), "Tax & Fees", ""),
)  # fmt: skip


def known_category(description: str) -> tuple[str, str] | None:
    """(category, subcategory) of a well-known Canadian public service or transit operator in the
    row's text, by the default category names; None for any other merchant."""
    text = _words(description)
    return next(((category, sub) for phrases, category, sub in _KNOWN if _has(text, phrases)), None)
