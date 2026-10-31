"""Security building blocks for the web app.

- SecurityHeadersMiddleware: strict Content Security Policy and friends on
  every response, so even if a hostile player name slipped into the page, the
  browser would refuse to run injected script.
- SameOriginMiddleware: refuses state-changing requests from other sites (CSRF).
- BodySizeLimitMiddleware: rejects oversized request bodies before they are read.
- RateLimiter: per-client sliding-window limits, answered with 429 + Retry-After.
- TTLCache: small, size-bounded cache so repeated lookups don't spend the
  Clash Royale API quota (and can't be used to fill the server's memory).
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict, defaultdict, deque
from typing import Any, Callable
from urllib.parse import urlsplit

from starlette.types import ASGIApp, Message, Receive, Scope, Send

CSP = "; ".join([
    "default-src 'none'",          # nothing is allowed unless listed below
    "script-src 'self'",           # only our own script file: no inline or injected script
    "style-src 'self'",
    "img-src 'self'",
    "connect-src 'self'",          # the page may only call our own API
    "base-uri 'none'",
    "form-action 'self'",
    "frame-ancestors 'none'",      # can't be framed (clickjacking)
])


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, *, hsts: bool = False):
        self.app = app
        self.headers = [
            (b"content-security-policy", CSP.encode()),
            (b"x-content-type-options", b"nosniff"),
            (b"referrer-policy", b"no-referrer"),
            (b"x-frame-options", b"DENY"),
            (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=()"),
            (b"cross-origin-opener-policy", b"same-origin"),
            (b"cross-origin-resource-policy", b"same-origin"),
        ]
        if hsts:  # only when served over HTTPS
            self.headers.append((b"strict-transport-security", b"max-age=31536000"))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                existing = {k.lower() for k, _ in message.get("headers", [])}
                message.setdefault("headers", [])
                message["headers"] = list(message["headers"]) + [
                    (k, v) for k, v in self.headers if k not in existing]
                if scope["path"].startswith("/api/"):
                    message["headers"].append((b"cache-control", b"no-store"))
            await send(message)

        await self.app(scope, receive, send_with_headers)


class SameOriginMiddleware:
    """Cross-site request forgery guard: any request that can change something
    (POST, PUT, PATCH, DELETE) must come from a page on this same site.

    Browsers always send an Origin header on such requests, and another site
    can't fake it, so a mismatch (or no Origin or Referer at all) is refused.
    SameSite=Lax cookies are a second layer on top of this.
    """

    SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] in self.SAFE_METHODS:
            await self.app(scope, receive, send)
            return
        headers = {k.lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        host = headers.get(b"host", "")
        source = headers.get(b"origin") or headers.get(b"referer") or ""
        if not host or source == "null" or urlsplit(source).netloc != host:
            await _send_json(send, 403, b'{"detail":"Cross-site request refused"}')
            return
        await self.app(scope, receive, send)


class BodySizeLimitMiddleware:
    """Reject request bodies over max_bytes with 413, whether or not they declare a length."""

    def __init__(self, app: ASGIApp, *, max_bytes: int = 4096):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        for name, value in scope.get("headers", []):
            if name == b"content-length" and value.isdigit() and int(value) > self.max_bytes:
                await _send_json(send, 413, b'{"detail":"Request body too large"}')
                return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _BodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _BodyTooLarge:
            await _send_json(send, 413, b'{"detail":"Request body too large"}')


class _BodyTooLarge(Exception):
    pass


async def _send_json(send: Send, status: int, body: bytes) -> None:
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


class RateLimiter:
    """Sliding-window limit per (client, bucket). Thread-safe, bounded memory."""

    def __init__(self, limits: dict[str, tuple[int, float]], *,
                 clock: Callable[[], float] = time.monotonic, max_clients: int = 10_000):
        self.limits = limits  # bucket -> (max requests, window seconds)
        self.clock = clock
        self.max_clients = max_clients
        self._hits: dict[tuple[str, str], deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, client: str, bucket: str) -> float | None:
        """Record a request. Returns None if allowed, else seconds until a retry is allowed."""
        limit, window = self.limits[bucket]
        now = self.clock()
        with self._lock:
            if len(self._hits) > self.max_clients:  # under a flood, start afresh rather than grow
                self._hits.clear()
            hits = self._hits[(client, bucket)]
            while hits and now - hits[0] >= window:
                hits.popleft()
            if len(hits) >= limit:
                return max(0.0, window - (now - hits[0]))
            hits.append(now)
            return None


class TTLCache:
    """Least-recently-used cache whose entries expire after ttl seconds."""

    def __init__(self, ttl: float, max_items: int = 1000,
                 clock: Callable[[], float] = time.monotonic):
        self.ttl, self.max_items, self.clock = ttl, max_items, clock
        self._data: OrderedDict[Any, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()

    def get_or_set(self, key: Any, compute: Callable[[], Any]) -> Any:
        now = self.clock()
        with self._lock:
            hit = self._data.get(key)
            if hit and now - hit[0] < self.ttl:
                self._data.move_to_end(key)
                return hit[1]
        value = compute()  # outside the lock: slow network calls don't block others
        with self._lock:
            self._data[key] = (now, value)
            self._data.move_to_end(key)
            while len(self._data) > self.max_items:
                self._data.popitem(last=False)
        return value
