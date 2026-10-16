"""Comparing store and merchant names (receipt matching, duplicate receipts)."""

import re
import unicodedata

_NOISE_WORDS = frozenset(
    {"the", "inc", "ltd", "store", "stores", "shop", "co", "corp", "com", "www", "pos", "sq"}
)


def name_words(text: str) -> set[str]:
    """Words of a store or merchant name, lower-cased and accent-free, without numbers and noise."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return {word for word in re.split(r"[^a-z]+", plain) if len(word) > 1 and word not in _NOISE_WORDS}


# Words banks put in front of the merchant ("RETAIL PURCHASE 000024305550 SUZUYA JAPANESE").
_BANK_PREFIXES = re.compile(
    r"^\s*(?:(?:visa\s+debit|interac|point\s+of\s+sale|pos|retail|debit|online|contactless)\s+)*"
    r"(?:purchase|payment|pmt)?\s*",
    re.IGNORECASE,
)
# Reference numbers and store numbers: 000024305550, #1234, *1234, masked card numbers.
_REFERENCES = re.compile(r"(?:[#*]\s*)?\b\d{4,}\b|#\s*\d+|\*{2,}\d*|\b(?=[A-Za-z0-9]*\d{4})[A-Za-z0-9]{6,}\b")
# Foreign-currency details at the end: "13.32 USD @ 1.421171".
_FOREIGN_AMOUNT = re.compile(r"\s+\d+(?:[.,]\d+)?\s+[A-Z]{3}\s*@\s*[\d.,]+\s*$", re.IGNORECASE)
# A letter run with apostrophes, for title case ("JAK'S" → "Jak's", "(KINGSWAY)" → "(Kingsway)").
_LETTERS = re.compile(r"[A-Za-z][A-Za-z']*")


def clean_bank_text(text: str) -> str:
    """The bank's description without purchase prefixes and reference numbers, in title case
    ("RETAIL PURCHASE 000024305550 SUZUYA JAPANESE" → "Suzuya Japanese"). Words in capitals get
    title case, except a two-letter code at the end (province or state, e.g. BC)."""
    plain = _FOREIGN_AMOUNT.sub("", text)
    plain = _REFERENCES.sub(" ", _BANK_PREFIXES.sub("", plain, count=1)).strip(" -–*:/.,@")
    plain = re.sub(r"\s+,", ",", plain)  # "GROWMUSHROO 2503276931, BC" → "Growmushroo, BC"
    if plain.count("(") > plain.count(")"):  # a bracket the bank cut off: "ROSEMARY BROWN ( BURNABY"
        plain = re.sub(r"\s*\((?!.*\))\s*", " ", plain)
    words = plain.split()
    if not words:
        return text.strip()
    # Words in capitals get title case; words the bank wrote in mixed case are kept.
    titled = [
        _LETTERS.sub(lambda m: m.group(0).capitalize(), w) if not any(c.islower() for c in w) else w
        for w in words
    ]
    if len(words[-1]) == 2 and words[-1].isalpha() and words[-1].isupper():
        titled[-1] = words[-1]  # a province or state code at the end: "Burnaby, BC"
    return " ".join(titled)


def display_name(merchant_name: str, description: str) -> str:
    """The name to show for a bank charge.

    Plaid's merchant name is tidier ("STARBUCKS #123 VANCOUVER" → "Starbucks") but is a guess,
    sometimes wrong ("SUZUYA JAPANESE MARKET" → "Suzuyo America Inc."). It is used only when its
    first meaningful token appears in the bank's own text (letters only, ignoring spaces and
    punctuation); otherwise the cleaned bank text is shown.
    """
    cleaned = clean_bank_text(description) if description.strip() else ""
    merchant = merchant_name.strip()
    first = _first_token(merchant)
    if merchant and (not cleaned or (first and first in _compact_letters(cleaned))):
        return merchant
    return cleaned or merchant


def _compact_letters(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z]+", "", plain)


# Words too common to identify a business on their own: "City of Vancouver" must agree with the
# bank's text on "Vancouver", not on "City" (the charge was CITY BBY..., the City of Burnaby).
_GENERIC_WORDS = frozenset(
    {
        "of", "and", "de", "la", "le", "les", "du", "des", "cie",
        "city", "town", "county", "district", "municipality", "province", "government",
        "university", "college", "school", "hospital", "bank", "church",
        "restaurant", "restaurants", "cafe", "coffee", "bar", "grill", "kitchen", "bakery",
        "market", "supermarket", "pharmacy", "gas", "station", "hotel", "inn", "services",
    }
)  # fmt: skip


def _first_token(name: str) -> str:
    """The merchant name's first meaningful token, letters only ("A&W Restaurants" → "aw"). It is
    the most distinctive part: sharing only a generic word ("Sushiya Japanese Restaurant" vs
    "SUZUYA JAPANESE") doesn't make it the same business."""
    for token in name.split():
        letters = _compact_letters(token)
        if len(letters) > 1 and letters not in _NOISE_WORDS and letters not in _GENERIC_WORDS:
            return letters
    return ""


def merchant_key(name: str) -> str:
    """A merchant's identity for remembered categories: its first two meaningful words, lower-case
    and accent-free ("Suzuya Japanese Market Burnaby, BC" and "Suzuya Japanese" → "suzuya japanese").
    Names without Latin words use the whole name without spaces and punctuation."""
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    words = [w for w in re.split(r"[^a-z]+", plain) if len(w) > 1 and w not in _NOISE_WORDS]
    if words:
        return " ".join(words[:2])
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", name).casefold())
