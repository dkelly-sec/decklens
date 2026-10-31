"""Turn display names into search keys, so fancy fonts, accents and emoji
don't stop you finding a player by typing the plain letters you can see.

    search_key("𝓓𝓪𝓿𝓲𝓭")    -> "david"
    search_key("Dávid🔥")   -> "david"
    search_key("Dаvid")     -> "david"   (Cyrillic 'а' mapped to Latin 'a')

Always store the original name for display and escape it when rendering:
names are attacker-controlled input.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

MAX_NAME_LENGTH = 64

# Look-alike letters people use in fancy names ("ʆʀaɴҡ", "ꜰʀᴀɴᴋ", "ƒяαηк").
# NFKC already turns maths letters, fullwidth and circled letters into plain
# ones; this covers what it doesn't:
# 1. Latin letters derived from their Unicode names: anything named like
#    "LATIN ... LETTER [SMALL CAPITAL|TURNED|...] R [WITH ...]" counts as "r".
# 2. Hand-picked Cyrillic, Greek and IPA letters that look like Latin ones.
# 3. Regional indicator letters (🇫🇷🇦🇳🇰), often used as fancy capitals.
# Used on both stored names and what you type, so typing the real letters
# (Cyrillic, say) still matches too.

_LATIN_NAME = re.compile(
    r"LATIN (?:SMALL |CAPITAL )?LETTER (?:SMALL CAPITAL |TURNED |REVERSED |INVERTED |SCRIPT "
    r"|CLOSED |OPEN |BARRED |DOTLESS |LONG |SIDEWAYS |BLACKLETTER |MIDDLE-WELSH )*"
    r"([A-Z])(?: WITH .*| |$)")
_LATIN_BLOCKS = [(0x0180, 0x024F), (0x0250, 0x02AF), (0x1D00, 0x1DBF), (0x2C60, 0x2C7F),
                 (0xA720, 0xA7FF), (0xAB30, 0xAB6F)]


def _derived_latin() -> dict[str, str]:
    table = {}
    for start, stop in _LATIN_BLOCKS:
        for code in range(start, stop + 1):
            ch = chr(code)
            m = _LATIN_NAME.match(unicodedata.name(ch, ""))
            if m:
                table[ch] = m.group(1).lower()
    return table


_CONFUSABLES = {
    **_derived_latin(),
    # Latin and IPA letters whose names don't spell a single Latin letter
    "ɑ": "a", "ɐ": "a", "ɒ": "a", "ə": "e", "ɘ": "e", "ɚ": "e", "ɜ": "e", "ɝ": "e",
    "ɣ": "y", "ɤ": "y", "ɩ": "i", "ʊ": "u", "ʋ": "v", "ʌ": "v", "ʒ": "z", "ʓ": "z",
    "ŋ": "n", "ƞ": "n", "ʃ": "f", "ʄ": "f", "ʆ": "f", "ƪ": "f", "ɸ": "o", "ɵ": "o",
    "ø": "o", "ð": "d", "đ": "d", "ħ": "h", "ł": "l", "ŧ": "t", "þ": "th", "æ": "ae",
    "œ": "oe", "ı": "i", "ȷ": "j", "ʁ": "r", "ʕ": "s", "ʔ": "",
    # Cyrillic
    "а": "a", "б": "b", "в": "b", "г": "r", "ґ": "r", "ғ": "f", "д": "d", "е": "e",
    "ё": "e", "є": "e", "ж": "x", "з": "z", "и": "u", "й": "u", "к": "k", "қ": "k",
    "ҝ": "k", "ҟ": "k", "ҡ": "k", "л": "n", "м": "m", "н": "h", "ң": "h", "һ": "h",
    "о": "o", "ө": "o", "п": "n", "р": "p", "с": "c", "ҫ": "c", "т": "t", "ҭ": "t",
    "у": "y", "ү": "y", "ұ": "y", "ў": "y", "ф": "f", "х": "x", "ҳ": "x", "ц": "u",
    "ч": "y", "ш": "w", "щ": "w", "ъ": "b", "ы": "bl", "ь": "b", "э": "e", "ю": "io",
    "я": "r", "ѕ": "s", "і": "i", "ї": "i", "ј": "j", "ӏ": "l", "ԁ": "d", "ԛ": "q",
    "ԝ": "w", "ѡ": "w", "ѵ": "v", "ѳ": "o", "ҩ": "a", "ӄ": "k", "ӈ": "h", "ӊ": "h",
    # Greek
    "α": "a", "ά": "a", "β": "b", "γ": "y", "δ": "d", "ε": "e", "έ": "e", "ζ": "z",
    "η": "n", "ή": "n", "θ": "o", "ι": "i", "ί": "i", "κ": "k", "λ": "l", "μ": "u",
    "ν": "v", "ξ": "e", "ο": "o", "ό": "o", "π": "n", "ρ": "p", "σ": "o", "ς": "c",
    "τ": "t", "υ": "u", "ύ": "u", "φ": "o", "χ": "x", "ψ": "w", "ω": "w", "ώ": "w",
    "ϲ": "c", "ϳ": "j", "ϱ": "p", "ϑ": "o", "ϐ": "b", "ϒ": "y",
}
# Alphabets from popular "fancy text" generators: Armenian, Canadian
# syllabics and Yi syllables that are used purely as look-alike letters.
_CONFUSABLES.update(dict(zip(
    "օսցհոռազքլ" "ᗩᗷᑕᗪᗴᕮᖴᘜᕼᓰᒍᛕᒪᗰᑎᑭᑫᖇᔕᑌᐯᗯ᙭ᖻᘔ" "ꍏꌃꉓꀸꍟꎇꁅꀍꀤꀭꀘ꒒ꂵꈤꂦꉣꆰꋪꌗ꓄ꀎꃴꅏꊼꌩꁴ",
    "oughnnwqpl" "abcdeefghijklmnpqrsuvwxyz" "abcdefghijklmnopqrstuvwxyz",
    strict=True,
)))
# Regional indicator symbols 🇦-🇿 (U+1F1E6-U+1F1FF) read as a-z.
_CONFUSABLES.update({chr(0x1F1E6 + i): chr(ord("a") + i) for i in range(26)})

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
        if ch and unicodedata.category(ch[0])[0] in ("L", "N"):  # letters and digits only
            out.append(ch)  # (a look-alike may map to "", e.g. the glottal stop: dropped)

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
