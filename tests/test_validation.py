import pytest

from decklens.validation import InvalidTagError, encode_tag, normalize_tag


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("#2PP", "#2PP"),
        ("2pp", "#2PP"),            # lowercase, no hash
        ("  #9QJ8UGVY  ", "#9QJ8UGVY"),
        ("#2PPO", "#2PP0"),         # letter O typed instead of zero
    ],
)
def test_valid_tags_are_normalised(raw, expected):
    assert normalize_tag(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "#",
        "#AB",                      # A and B are not in the tag alphabet
        "#2P",                      # too short
        "#" + "2" * 16,             # too long
        "#2PP/../clans",            # path traversal attempt
        "#2PP?limit=1000",          # query injection attempt
        "#2PP'; DROP TABLE players;--",
        "#2PP%00",
        "x" * 10_000,               # oversized input rejected before regex work
        None,
        12345,
    ],
)
def test_invalid_tags_are_rejected(raw):
    with pytest.raises(InvalidTagError):
        normalize_tag(raw)


def test_encode_tag_percent_encodes_hash():
    assert encode_tag("#2PP") == "%232PP"
