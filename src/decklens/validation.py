"""Strict allowlist validation for untrusted player input.

Player tags only ever use the characters 0289PYLQGRJCUV. Anything else is
rejected before it can reach a database query or an upstream URL.
"""

from __future__ import annotations

import re
from urllib.parse import quote

TAG_ALPHABET = "0289PYLQGRJCUV"
_MAX_RAW_LENGTH = 32
_TAG_RE = re.compile(rf"#[{TAG_ALPHABET}]{{3,15}}")


class InvalidTagError(ValueError):
    """Raised for anything that is not a well-formed player or clan tag."""


def normalize_tag(raw: object) -> str:
    """Return a canonical tag like '#2PP' or raise InvalidTagError.

    Accepts lowercase, a missing '#', surrounding spaces, and the letter O
    typed instead of zero (tags never contain the letter O).
    """
    if not isinstance(raw, str):
        raise InvalidTagError("tag must be a string")
    if len(raw) > _MAX_RAW_LENGTH:  # cheap length check before any other work
        raise InvalidTagError("tag is too long")

    tag = raw.strip().upper().replace("O", "0")
    if not tag.startswith("#"):
        tag = "#" + tag
    if not _TAG_RE.fullmatch(tag):
        raise InvalidTagError("tag contains invalid characters or has the wrong length")
    return tag


def encode_tag(raw: object) -> str:
    """Validate a tag and percent-encode it for use in a URL path ('#' -> '%23')."""
    return quote(normalize_tag(raw), safe="")
