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

    # --- internals ---------------------------------------------------------

    def _backoff(self, response: requests.Response | None, attempt: int) -> float:
        if response is not None:
            retry_after = response.headers.get("Retry-After", "")
            if retry_after.isdigit():
                return min(float(retry_after), _MAX_BACKOFF_SECONDS)
        base = min(2.0**attempt, _MAX_BACKOFF_SECONDS)
        return base + _jitter.uniform(0, 0.5)

    def _get(self, path: str) -> Any:
        url = BASE_URL + path
        for attempt in range(self._max_retries + 1):
            last_try = attempt == self._max_retries
            try:
                response = self._session.get(url, timeout=self._timeout, allow_redirects=False)
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
