"""Card images, served from our own server.

The browser never loads anything from another site (the CSP only allows
'self'), so card art is fetched here and passed through. That route is a
classic SSRF risk, so it is locked down:

- The browser only sends a card id. Image URLs come from the official card
  list (GET /cards), never from the request.
- Only https://api-assets.clashroyale.com is allowed, with a strict path shape.
- A separate HTTP session is used, so the API key is never sent to the asset host.
- No redirects, a 5 second timeout, PNG only (checked by header AND file
  signature), and at most 256 KB, read in chunks so a huge reply can't fill memory.
- Results are cached, so each image is fetched once per day at most.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Callable
from urllib.parse import urlsplit

import requests

from .security import TTLCache

log = logging.getLogger("decklens.web.cards")

ASSET_HOST = "api-assets.clashroyale.com"
_ASSET_PATH = re.compile(r"/[a-z0-9-]{1,40}/\d{1,4}/[A-Za-z0-9_-]{1,160}\.png")
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_IMAGE_BYTES = 256 * 1024
# (our key, field in the API's iconUrls)
ICON_FIELDS = (("medium", "medium"), ("evo", "evolutionMedium"), ("hero", "heroMedium"))


def safe_asset_url(url: Any) -> str | None:
    """The URL if it is an official Clash Royale card image URL, else None."""
    if not isinstance(url, str) or len(url) > 300:
        return None
    parts = urlsplit(url)
    if (parts.scheme != "https" or parts.hostname != ASSET_HOST or parts.port is not None
            or parts.username or parts.password or parts.query or parts.fragment
            or not _ASSET_PATH.fullmatch(parts.path)):
        return None
    return url


class CardImages:
    def __init__(self, client: Any, *, session: requests.Session | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self._client = client
        self._session = session or requests.Session()  # no Authorization header, ever
        self._catalog = TTLCache(ttl=6 * 3600, max_items=1, clock=clock)
        self._images = TTLCache(ttl=24 * 3600, max_items=400, clock=clock)

    def catalog(self) -> dict[int, dict[str, str]]:
        """card id -> {"medium": url, "evo": url, "hero": url}, only URLs that pass the checks."""
        def build() -> dict[int, dict[str, str]]:
            out: dict[int, dict[str, str]] = {}
            rejected = 0
            for card in self._client.get_cards():
                cid, icons = card.get("id"), card.get("iconUrls")
                if not isinstance(cid, int) or isinstance(cid, bool) or not isinstance(icons, dict):
                    continue
                urls: dict[str, str] = {}
                for key, field in ICON_FIELDS:
                    raw = icons.get(field)
                    if raw is None:
                        continue
                    url = safe_asset_url(raw)
                    if url:
                        urls[key] = url
                    else:
                        rejected += 1
                if urls:
                    out[cid] = urls
            if rejected:  # never log the URLs themselves: they are untrusted input
                log.warning("ignored %d card image URLs that failed the safety checks", rejected)
            return out

        return self._catalog.get_or_set("catalog", build)

    def image(self, card_id: int, form: str | None = None) -> bytes | None:
        """PNG for a card, in its Evolution or Hero art when asked and available."""
        urls = self.catalog().get(card_id)
        if not urls:
            return None
        url = urls.get(form) if form in ("evo", "hero") and form in urls else urls.get("medium")
        if url is None:
            return None
        try:
            return self._images.get_or_set(url, lambda: self._fetch(url))
        except _BadImage as exc:
            log.warning("rejected card image for %s: %s", card_id, exc)
            return None

    def _fetch(self, url: str) -> bytes:
        if safe_asset_url(url) is None:  # checked again right before use
            raise _BadImage("URL not allowed")
        try:
            response = self._session.get(url, timeout=5, allow_redirects=False, stream=True,
                                         headers={"Accept": "image/png"})
        except requests.RequestException as exc:
            raise _BadImage(type(exc).__name__) from None
        try:
            if response.status_code != 200:
                raise _BadImage(f"HTTP {response.status_code}")
            content_type = response.headers.get("Content-Type", "").split(";")[0].strip()
            if content_type != "image/png":
                raise _BadImage(f"unexpected type {content_type!r}")
            body = bytearray()
            for chunk in response.iter_content(chunk_size=16384):
                body.extend(chunk)
                if len(body) > MAX_IMAGE_BYTES:
                    raise _BadImage("too large")
            if not bytes(body).startswith(_PNG_SIGNATURE):
                raise _BadImage("not a PNG")
            return bytes(body)
        finally:
            response.close()


class _BadImage(Exception):
    pass
