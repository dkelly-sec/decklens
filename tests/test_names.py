import pytest

from decklens.names import MAX_NAME_LENGTH, search_key


@pytest.mark.parametrize(
    "name",
    [
        "David",
        "DAVID",
        "𝓓𝓪𝓿𝓲𝓭",            # mathematical script letters
        "ＤＡＶＩＤ",            # full-width letters
        "Dávid",                # accent
        "Dаvid",                # Cyrillic 'а'
        "🔥David🔥",            # emoji
        "★ D a v i d ★",        # symbols and spaces
        "Đavid",                # Latin letter without a decomposition
    ],
)
def test_variants_share_a_search_key(name):
    assert search_key(name) == "david"


def test_digits_are_kept():
    assert search_key("xX_Sniper99_Xx") == "xxsniper99xx"


def test_emoji_only_name_is_unsearchable():
    assert search_key("🔥👑🔥") == ""


def test_scripts_without_latin_letters_are_kept():
    assert search_key("철수") == "철수"
    assert search_key("たかし") == "たかし"


def test_prefix_of_a_name_matches_prefix_of_its_key():
    assert search_key("철수").startswith(search_key("철"))
    assert search_key("がっこう").startswith(search_key("が"))


def test_japanese_voicing_marks_are_preserved():
    assert search_key("が") != search_key("か")


def test_markup_in_names_is_stripped_from_the_key():
    assert search_key("<script>alert(1)</script>") == "scriptalert1script"


def test_long_input_is_truncated():
    assert len(search_key("a" * 100_000)) == MAX_NAME_LENGTH


def test_non_string_input():
    assert search_key(None) == ""


@pytest.mark.parametrize("query, name, low, high", [
    ("kau", "+Kaua", 1.0, 1.0),                    # appears unbroken: full marks
    ("kua", "+Kaua", 0.8, 0.95),                   # swapped / missing letters
    ("persainwarior", "Persian Warrior", 0.85, 0.95),
    ("gokuu", "goku", 0.85, 0.95),
    ("aj", "Masky", 0.0, 0.5),                     # unrelated: stays low
    ("kaua", "Pedro", 0.0, 0.1),
    ("", "Pedro", 0.0, 0.0), ("kaua", "", 0.0, 0.0),
])
def test_fuzzy_score(query, name, low, high):
    from decklens.names import fuzzy_score

    assert low <= fuzzy_score(search_key(query), search_key(name)) <= high


@pytest.mark.parametrize("fancy", [
    "ʆʀaɴҡ",          # esh with curl, small capitals, Bashkir ka (a real opponent)
    "ꜰʀᴀɴᴋ",          # small capitals
    "ƒяαηк",          # Latin f with hook, Cyrillic ya, Greek alpha and eta, Cyrillic ka
    "🇫🇷🇦🇳🇰",          # regional indicator letters
    "ᖴᖇᗩᑎK",          # Canadian syllabics from fancy-text generators
    "𝓕𝓻𝓪𝓷𝓴", "ⓕⓡⓐⓝⓚ", "Ｆｒａｎｋ",  # handled by NFKC
])
def test_fancy_alphabets_read_as_plain_letters(fancy):
    assert search_key(fancy) == "frank"


def test_real_scripts_still_match_themselves():
    # Typing the real letters gives the same key as the stored name.
    for name in ("محمد", "李小龍", "ДМИТРИЙ", "Ελένη"):
        assert search_key(name) == search_key(name.lower()) and search_key(name)


def test_every_character_is_handled_without_crashing():
    """Regression: a look-alike mapped to "" crashed the normaliser (and so the
    crawler and search) on any name containing it, e.g. "ʔ"."""
    from decklens.names import _CONFUSABLES, fuzzy_score

    assert search_key("ʔ") == "" and search_key("aʔb") == "ab"
    for ch in _CONFUSABLES:
        key = search_key(f"x{ch}y")
        assert key.startswith("x") and key.endswith("y")
    # Every code point up to U+FFFF (minus surrogates) plus emoji and flags.
    sample = [chr(c) for c in range(0x20, 0x10000) if not 0xD800 <= c <= 0xDFFF]
    sample += [chr(c) for c in range(0x1F1E6, 0x1F700)]
    for start in range(0, len(sample), 50):
        chunk = "".join(sample[start:start + 50])
        key = search_key(chunk)
        assert isinstance(key, str)
        fuzzy_score("frank", key)
