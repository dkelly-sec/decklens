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
