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
    r"^\s*(?:(?:intl|international|visa|debit|deb|interac|point\s+of\s+sale|pos|retail|online|contactless)\s+)*"
    r"(?:purchase|payment|pmt)?\s*",
    re.IGNORECASE,
)
# Order codes after a star (AMAZON.CA*2G9HF48P3) and phone numbers (866-216-1072).
_CODES = re.compile(r"\*\s*[A-Za-z0-9]*\d[A-Za-z0-9]*|\b\d{3}-\d{3}-?\d{0,4}")
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
    plain = _CODES.sub(" ", _FOREIGN_AMOUNT.sub("", text))
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


def display_name(merchant_name: str, description: str, confidence: str = "") -> str:
    """The name to show for a bank charge.

    Plaid's merchant name is tidier ("STARBUCKS #123 VANCOUVER" → "Starbucks") but is a guess,
    sometimes wrong ("SUZUYA JAPANESE MARKET" → "Suzuyo America Inc."), even when Plaid rates the
    match HIGH. It is used only when it agrees with the bank's own text (see _agrees; for Plaid's
    VERY_HIGH matches, one shared word is enough); otherwise the cleaned bank text is shown.
    """
    cleaned = clean_bank_text(description) if description.strip() else ""
    merchant = merchant_name.strip()
    if not merchant or not cleaned:
        return merchant or cleaned
    if confidence.upper() == "VERY_HIGH":
        # Plaid's very-high matches are known chains (Safeway, Esso, Real Canadian Superstore) and
        # the bank often abbreviates them ("REAL CDN SUPERS", "KELOWNA ESSO"), so one shared
        # distinctive word in either direction is enough. Even these can be wrong ("New York Life"
        # for "NYF #3304", New York Fries), so some agreement is still required.
        return merchant if _shares_a_word(merchant, cleaned) else cleaned
    return merchant if _agrees(merchant, cleaned) else cleaned


def _shares_a_word(merchant: str, bank_text: str) -> bool:
    """Plaid's first distinctive word is in the bank's text, or the bank's is in Plaid's name."""
    tokens = _distinctive_tokens(merchant)
    first = _first_bank_word(bank_text)
    return bool(tokens and _found(tokens[0], bank_text, cut_off_words=True)) or bool(
        first and _found(first, merchant, cut_off_words=False)
    )


def _compact_letters(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z]+", "", plain)


def _agrees(merchant: str, bank_text: str) -> bool:
    """Plaid's name and the bank's text agree both ways (generic words don't count):

    - every distinctive word of Plaid's name is in the bank's text: not "Triple O Construction"
      for "TRIPLE O # 570 KINGSWAY"; a bank word cut short counts ("BATH BODY WOR" for Bath &
      Body Works);
    - the bank text's first distinctive word is in Plaid's name: not "Whistler's Inn" for
      "EARLS WHISTLER", nor "L M Hanna Co" for "HANNAM METROTOW".
    """
    tokens = _distinctive_tokens(merchant)
    if not tokens or not all(_found(token, bank_text, cut_off_words=True) for token in tokens):
        return False
    first = _first_bank_word(bank_text)
    # Bank text without a distinctive word: the first check is all there is.
    return first is None or _found(first, merchant, cut_off_words=False)


def _first_bank_word(bank_text: str) -> str | None:
    """The bank text's first distinctive word: the first run of 2+ letters that isn't a noise or
    generic word ("Amazon.ca*2G9HF48P3 …" → "amazon", "THE RAMEN BUTCHER" → "ramen"). Letters
    joined by "&" are one word ("A & W" and "A&W" → "aw")."""
    plain = unicodedata.normalize("NFKD", bank_text).encode("ascii", "ignore").decode().lower()
    plain = re.sub(r"\b([a-z])\s*&\s*([a-z])\b", r"\1\2", plain)
    for word in re.findall(r"[a-z]{2,}", plain):
        if word not in _NOISE_WORDS and word not in _GENERIC_WORDS:
            return str(word)
    return None


def _found(token: str, text: str, cut_off_words: bool) -> bool:
    """`token` is in `text` (letters only). With `cut_off_words` (text is the bank's, which cuts
    long names short), a text word that is the start of `token` counts too."""
    if token in _compact_letters(text):
        return True
    words = [w for w in (_compact_letters(word) for word in text.split()) if len(w) >= 3]
    return cut_off_words and any(token.startswith(word) for word in words)


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


def _distinctive_tokens(name: str) -> list[str]:
    """A name's tokens, letters only ("A&W" → "aw"), without one-letter, noise, and generic words."""
    tokens = []
    for token in name.split():
        letters = _compact_letters(token)
        if len(letters) > 1 and letters not in _NOISE_WORDS and letters not in _GENERIC_WORDS:
            tokens.append(letters)
    return tokens


def merchant_key(name: str) -> str:
    """A merchant's identity for remembered categories: its first two meaningful words, lower-case
    and accent-free ("Suzuya Japanese Market Burnaby, BC" and "Suzuya Japanese" → "suzuya japanese").
    Names without Latin words use the whole name without spaces and punctuation."""
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    words = [w for w in re.split(r"[^a-z]+", plain) if len(w) > 1 and w not in _NOISE_WORDS]
    if words:
        return " ".join(words[:2])
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", name).casefold())


def bank_key(description: str) -> str:
    """The merchant key of a charge's own bank text (not of Plaid's guess), so it's the same
    whenever the charge is imported: the key for names learned from receipts."""
    return merchant_key(clean_bank_text(description))
