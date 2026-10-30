"""Minimal client for the official Clash Royale API.

Security properties:
- The base URL is a constant; user input only ever fills in a validated,
  percent-encoded tag, so it cannot change which host or path is called (SSRF).
- The API token is sent only in the Authorization header, never logged, and
  hidden from repr(). Redirects are refused so the token is never forwarded.
- Every request has a timeout, and 429/5xx responses are retried with
  capped exponential backoff, honouring Retry-After.
"""

from __future__ import annotations

import logging
import re
import secrets
import time
from typing import Any, Callable

import requests

from .validation import encode_tag

log = logging.getLogger(__name__)

BASE_URL = "https://api.clashroyale.com/v1"
_RETRYABLE = {429, 500, 502, 503, 504}
_MAX_BACKOFF_SECONDS = 60.0
_jitter = secrets.SystemRandom()

_PAGE_SIZE = 100
_MAX_PAGED_ITEMS = 10_000
_LOCATION_RE = re.compile(r"global|\d{1,10}")
_SEASON_RE = re.compile(r"[A-Za-z0-9_-]{1,20}")
_CURSOR_RE = re.compile(r"[A-Za-z0-9_=-]{1,512}")


def _validate_location_id(value: str) -> str:
    value = str(value)
    if not _LOCATION_RE.fullmatch(value):
        raise ValueError("location id must be 'global' or a numeric id")
    return value


def _validate_season_id(value: str) -> str:
    value = str(value)
    if not _SEASON_RE.fullmatch(value):
        raise ValueError("invalid season id")
    return value


class ApiError(Exception):
    """Any failed API call. Messages never contain the token."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class NotFoundError(ApiError):
    """The player or clan tag does not exist."""


class ClashRoyaleClient:
    def __init__(
        self,
        token: str,
        *,
        timeout: float = 10.0,
        max_retries: int = 4,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not token:
            raise ValueError("API token is required")
        self._session = session or requests.Session()
        self._session.headers.update(
            {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        )
        self._timeout = timeout
        self._max_retries = max_retries
        self._sleep = sleep

    def __repr__(self) -> str:  # never expose the token
        return f"ClashRoyaleClient(base_url={BASE_URL!r})"

    # --- public endpoints -------------------------------------------------

    def get_player(self, tag: str) -> dict[str, Any]:
        return self._get(f"/players/{encode_tag(tag)}")

    def get_battle_log(self, tag: str) -> list[dict[str, Any]]:
        data = self._get(f"/players/{encode_tag(tag)}/battlelog")
        if not isinstance(data, list):
            raise ApiError("unexpected battle log format")
        return data

    def get_ranked_leaderboard(
        self, location_id: str = "global", *, max_items: int = 1000
    ) -> list[dict[str, Any]]:
        """Current-season Ranked (Path of Legend) leaderboard for a location."""
        loc = _validate_location_id(location_id)
        return self._get_paged(f"/locations/{loc}/pathoflegend/players", max_items)

    def get_ranked_season_leaderboard(
        self, season_id: str, *, max_items: int = 1000
    ) -> list[dict[str, Any]]:
        """Final Ranked leaderboard for a finished season."""
        sid = _validate_season_id(season_id)
        return self._get_paged(f"/locations/global/pathoflegend/{sid}/rankings/players", max_items)

    def get_cards(self) -> list[dict[str, Any]]:
        """Every card in the game, with names, rarities and official image URLs."""
        data = self._get("/cards")
        items = data.get("items") if isinstance(data, dict) else None
        return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []

    def get_seasons(self) -> list[dict[str, Any]]:
        data = self._get("/locations/global/seasonsV2")
        items = data.get("items") if isinstance(data, dict) else None
        return items if isinstance(items, list) else []

    # --- internals ---------------------------------------------------------

    def _get_paged(self, path: str, max_items: int) -> list[dict[str, Any]]:
        """Follow the API's cursor paging until max_items or the last page."""
        max_items = max(0, min(int(max_items), _MAX_PAGED_ITEMS))
        items: list[dict[str, Any]] = []
        after: str | None = None
        while len(items) < max_items:
            params: dict[str, Any] = {"limit": min(_PAGE_SIZE, max_items - len(items))}
            if after:
                params["after"] = after
            data = self._get(path, params)
            page = data.get("items") if isinstance(data, dict) else None
            if not isinstance(page, list) or not page:
                break
            items.extend(i for i in page if isinstance(i, dict))
            cursors = ((data.get("paging") or {}).get("cursors") or {})
            after = cursors.get("after")
            if not isinstance(after, str) or not _CURSOR_RE.fullmatch(after):
                break  # no next page, or a cursor we don't trust
        return items[:max_items]

    def _backoff(self, response: requests.Response | None, attempt: int) -> float:
        if response is not None:
            retry_after = response.headers.get("Retry-After", "")
            if retry_after.isdigit():
                return min(float(retry_after), _MAX_BACKOFF_SECONDS)
        base = min(2.0**attempt, _MAX_BACKOFF_SECONDS)
        return base + _jitter.uniform(0, 0.5)

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = BASE_URL + path
        for attempt in range(self._max_retries + 1):
            last_try = attempt == self._max_retries
            try:
                response = self._session.get(
                    url, params=params, timeout=self._timeout, allow_redirects=False
                )
            except requests.RequestException as exc:
                if last_try:
                    raise ApiError(f"network error calling {path}: {type(exc).__name__}") from None
                self._sleep(self._backoff(None, attempt))
                continue

            status = response.status_code
            if status == 200:
                return response.json()
            if status in _RETRYABLE and not last_try:
                delay = self._backoff(response, attempt)
                log.warning("HTTP %s on %s, retrying in %.1fs", status, path, delay)
                self._sleep(delay)
                continue
            if status == 404:
                raise NotFoundError(f"not found: {path}", status)
            if status == 403:
                raise ApiError(
                    "403 forbidden: the API key is invalid or this machine's IP "
                    "is not whitelisted for it",
                    status,
                )
            raise ApiError(f"HTTP {status} calling {path}", status)

        raise ApiError(f"gave up calling {path}")  # pragma: no cover
