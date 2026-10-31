"""Turn display names into search keys, so fancy fonts, accents and emoji
don't stop you finding a player by typing the plain letters you can see.

    search_key("𝓓𝓪𝓿𝓲𝓭")    -> "david"
    search_key("Dávid🔥")   -> "david"
    search_key("Dаvid")     -> "david"   (Cyrillic 'а' mapped to Latin 'a')

Always store the original name for display and escape it when rendering:
names are attacker-controlled input.
"""

from __future__ import annotations

import unicodedata
from difflib import SequenceMatcher

MAX_NAME_LENGTH = 64

# A small, hand-picked subset of Unicode look-alikes (UTS #39 confusables),
# plus Latin letters that have no Unicode decomposition. Replace with the full
# confusables table later (e.g. ICU's SpoofChecker skeleton).
_CONFUSABLES = {
    # Cyrillic
    "а": "a", "в": "b", "е": "e", "ѕ": "s", "і": "i", "ј": "j", "к": "k",
    "м": "m", "н": "h", "о": "o", "р": "p", "с": "c", "т": "t", "у": "y",
    "х": "x", "ԁ": "d", "ԛ": "q", "ԝ": "w",
    # Greek
    "α": "a", "β": "b", "ε": "e", "ι": "i", "κ": "k", "ν": "v", "ο": "o",
    "ρ": "p", "τ": "t", "υ": "u", "χ": "x",
    # Latin letters without a decomposition
    "đ": "d", "ð": "d", "ħ": "h", "ł": "l", "ø": "o", "ŧ": "t", "æ": "ae",
    "œ": "oe", "þ": "th", "ɡ": "g", "ı": "i",
}

# Combining marks are only stripped from Latin, Greek and Cyrillic letters
# (code points below U+0530). Elsewhere, such as Japanese voicing marks,
# they change the letter, so they are kept.
_STRIP_MARKS_BELOW = 0x0530


def search_key(name: str) -> str:
    """Return a normalised, lowercase key containing only letters and digits."""
    if not isinstance(name, str):
        return ""
    text = unicodedata.normalize("NFKC", name[:MAX_NAME_LENGTH]).casefold()
    text = unicodedata.normalize("NFKD", text)

    out: list[str] = []
    for ch in text:
        if unicodedata.combining(ch):
            if not out or ord(out[-1][-1]) < _STRIP_MARKS_BELOW:
                continue  # drop stray marks and accents on Latin/Greek/Cyrillic letters
            out.append(ch)
            continue
        ch = _CONFUSABLES.get(ch, ch)
        if unicodedata.category(ch[0])[0] in ("L", "N"):  # letters and digits only
            out.append(ch)

    return unicodedata.normalize("NFC", "".join(out))


def fuzzy_score(query_key: str, key: str) -> float:
    """How well a typed (normalised) query matches a (normalised) name, 0-1.

    1.0 if it appears unbroken anywhere in the name; otherwise the best
    similarity between the query and any same-length part of the name (or the
    whole name), so partial names, swapped and missing letters still score
    well: "kua" vs "+kaua" -> 0.86, "persainwarrior" vs "persianwarrior" -> 0.93.
    """
    if not query_key or not key:
        return 0.0
    if query_key in key:
        return 1.0
    best = SequenceMatcher(None, query_key, key).ratio()
    width = len(query_key)
    for start in range(max(0, len(key) - width) + 1):
        best = max(best, SequenceMatcher(None, query_key, key[start:start + width + 1]).ratio())
    return best
