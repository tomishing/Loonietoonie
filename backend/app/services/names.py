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
