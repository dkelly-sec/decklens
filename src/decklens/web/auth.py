"""Logins for the web app.

Design (see THREAT_MODEL.md):
- Accounts live in the web_users table and are managed only with the
  command-line tool (python -m decklens.users). There is no sign-up page, and
  the website's database role can READ that table but never change it.
- Passwords are hashed with scrypt (standard library, OWASP parameters:
  N=2^17, r=8, p=1) with a random 16-byte salt each, and compared in
  constant time. Unknown usernames are checked against a dummy hash so the
  response time doesn't reveal which usernames exist.
- A session is a signed cookie (HMAC-SHA256 over username, session version and
  issue time) that is HttpOnly, SameSite=Lax, Secure with the __Host- prefix
  over HTTPS, and valid for 30 days. Bumping a user's session version (the
  "logout-all" command, or a password change) invalidates every cookie they hold.
- After 5 wrong passwords for a username within 15 minutes, that username is
  locked for 15 minutes, whether or not it exists, so lockouts can't be used
  to discover usernames either. Per-IP rate limits sit on top of this.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Callable

USERNAME_RE = re.compile(r"[a-z0-9_.-]{3,32}")
MIN_PASSWORD = 12
MAX_PASSWORD = 128
SESSION_DAYS = 30
MIN_SECRET_LENGTH = 32

# scrypt cost: 2^17 x 8 x 128 bytes = 128 MB of memory per hash, about 0.2 s.
_SCRYPT_LOG_N, _SCRYPT_R, _SCRYPT_P = 17, 8, 1
_SCRYPT_MAXMEM = 256 * 1024 * 1024
_MAX_LOG_N = 20  # refuse stored hashes that would cost absurd amounts to check


def normalise_username(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None
    name = raw.strip().lower()
    return name if USERNAME_RE.fullmatch(name) else None


def check_new_password(password: str) -> None:
    """Rules for new passwords (NIST-style: length over complexity rules)."""
    if not MIN_PASSWORD <= len(password) <= MAX_PASSWORD:
        raise ValueError(f"passwords must be {MIN_PASSWORD}-{MAX_PASSWORD} characters")
    if len(set(password)) < 5:
        raise ValueError("that password is too repetitive")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _scrypt(password: str, salt: bytes, log_n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=2 ** log_n, r=r, p=p,
                          maxmem=_SCRYPT_MAXMEM, dklen=32)


def hash_password(password: str, *, log_n: int = _SCRYPT_LOG_N) -> str:
    """scrypt hash with a fresh salt. log_n is only lowered by tests, for speed."""
    salt = secrets.token_bytes(16)
    digest = _scrypt(password, salt, log_n, _SCRYPT_R, _SCRYPT_P)
    return f"scrypt${log_n}${_SCRYPT_R}${_SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    """True if password matches the stored hash. Malformed hashes never match."""
    try:
        scheme, log_n, r, p, salt, digest = stored.split("$")
        log_n, r, p = int(log_n), int(r), int(p)
        if scheme != "scrypt" or not (14 <= log_n <= _MAX_LOG_N and 1 <= r <= 16 and p == 1):
            return False
        expected = _unb64(digest)
        actual = _scrypt(password, _unb64(salt), log_n, r, p)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


# A real hash of a random password, checked when the username doesn't exist,
# so failed logins take the same time either way.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


@dataclass(frozen=True)
class User:
    username: str
    password_hash: str
    session_version: int
    disabled: bool = False


class SessionSigner:
    """Makes and checks signed session cookie values."""

    def __init__(self, secret: str, *, lifetime: float = SESSION_DAYS * 86400,
                 clock: Callable[[], float] = time.time):
        if len(secret) < MIN_SECRET_LENGTH:
            raise ValueError(f"the session secret must be at least {MIN_SECRET_LENGTH} characters")
        self._key = hashlib.sha256(b"decklens-session|" + secret.encode()).digest()
        self.lifetime, self.clock = lifetime, clock

    def _sign(self, payload: str) -> str:
        return _b64(hmac.new(self._key, payload.encode(), hashlib.sha256).digest())

    def make(self, user: User) -> str:
        body = json.dumps({"u": user.username, "v": user.session_version,
                           "t": int(self.clock())}, separators=(",", ":"))
        payload = _b64(body.encode())
        return f"{payload}.{self._sign(payload)}"

    def read(self, value: str | None) -> tuple[str, int] | None:
        """(username, session version) from a valid, unexpired cookie, else None."""
        if not value or len(value) > 512 or value.count(".") != 1:
            return None
        payload, signature = value.split(".")
        if not hmac.compare_digest(signature, self._sign(payload)):
            return None
        try:
            data = json.loads(_unb64(payload))
            username, version, issued = data["u"], data["v"], data["t"]
        except (ValueError, TypeError, KeyError):
            return None
        if not (isinstance(username, str) and isinstance(version, int)
                and isinstance(issued, int)):
            return None
        age = self.clock() - issued
        if age < -60 or age > self.lifetime:  # expired, or issued in the future
            return None
        return username, version


class LoginGuard:
    """Locks a username after too many failures, whether or not it exists."""

    def __init__(self, *, max_failures: int = 5, window: float = 900.0,
                 clock: Callable[[], float] = time.monotonic, max_tracked: int = 10_000):
        self.max_failures, self.window, self.clock = max_failures, window, clock
        self.max_tracked = max_tracked
        self._failures: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def _recent(self, key: str, now: float) -> deque:
        failures = self._failures[key]
        while failures and now - failures[0] >= self.window:
            failures.popleft()
        return failures

    def locked_for(self, key: str) -> float:
        """Seconds until this username may try again (0 if it may now)."""
        now = self.clock()
        with self._lock:
            failures = self._recent(key, now)
            if len(failures) < self.max_failures:
                return 0.0
            return max(0.0, self.window - (now - failures[0]))

    def failed(self, key: str) -> None:
        with self._lock:
            if len(self._failures) > self.max_tracked:  # bounded memory under a flood
                self._failures.clear()
            self._recent(key, self.clock()).append(self.clock())

    def succeeded(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)


class Auth:
    """Everything the web app needs for logins."""

    def __init__(self, load_user: Callable[[str], User | None], secret: str, *,
                 secure_cookie: bool, clock: Callable[[], float] = time.time,
                 guard: LoginGuard | None = None, user_cache_seconds: float = 30.0):
        self._load_user = load_user
        self.signer = SessionSigner(secret, clock=clock)
        self.secure_cookie = secure_cookie
        # The __Host- prefix makes browsers insist on Secure, Path=/ and no Domain.
        self.cookie_name = "__Host-decklens_session" if secure_cookie else "decklens_session"
        self.guard = guard or LoginGuard()
        self.clock = clock
        self._cache: dict[str, tuple[float, User | None]] = {}
        self._cache_seconds = user_cache_seconds
        self._lock = threading.Lock()

    def _user(self, username: str, *, fresh: bool = False) -> User | None:
        now = self.clock()
        with self._lock:
            hit = self._cache.get(username)
            if hit and not fresh and now - hit[0] < self._cache_seconds:
                return hit[1]
        user = self._load_user(username)
        with self._lock:
            if len(self._cache) > 1000:
                self._cache.clear()
            self._cache[username] = (now, user)
        return user

    def login(self, raw_username: Any, password: Any) -> tuple[User | None, float]:
        """(user, 0) on success; (None, 0) for wrong details; (None, seconds) when locked."""
        key = raw_username.strip().lower()[:64] if isinstance(raw_username, str) else ""
        wait = self.guard.locked_for(key)
        if wait:
            return None, wait
        username = normalise_username(raw_username)
        password = password if isinstance(password, str) and len(password) <= MAX_PASSWORD else ""
        user = self._user(username, fresh=True) if username else None
        ok = verify_password(password, user.password_hash if user else _DUMMY_HASH)
        if ok and user and not user.disabled and password:
            self.guard.succeeded(key)
            return user, 0.0
        self.guard.failed(key)
        return None, 0.0

    def session_user(self, cookie: str | None) -> User | None:
        found = self.signer.read(cookie)
        if found is None:
            return None
        username, version = found
        user = self._user(username)
        if user is None or user.disabled or user.session_version != version:
            return None
        return user

    def cookie_header(self, value: str, *, max_age: int) -> str:
        parts = [f"{self.cookie_name}={value}", "Path=/", f"Max-Age={max_age}",
                 "HttpOnly", "SameSite=Lax"]
        if self.secure_cookie:
            parts.append("Secure")
        return "; ".join(parts)

    def login_cookie(self, user: User) -> str:
        return self.cookie_header(self.signer.make(user), max_age=int(self.signer.lifetime))

    def logout_cookie(self) -> str:
        return self.cookie_header("", max_age=0)


_LOAD_USER = """
    SELECT username, password_hash, session_version, disabled
    FROM web_users WHERE username = %s
"""


def database_user_loader(connect: Callable[[], Any]) -> Callable[[str], User | None]:
    """Look users up with the web app's read-only database connection."""
    def load(username: str) -> User | None:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(_LOAD_USER, (username,))
            row = cur.fetchone()
        return User(*row) if row else None
    return load
