"""Hand-off from the screen watcher to the DeckLens page.

The watcher (python -m decklens.watcher, on the PC showing the mirrored phone)
reads the opponent's name and clan off the battle screen and POSTs just that
text here. The page, logged in as the same user, polls for it and runs the
search. Only text ever arrives; screenshots never leave the watcher's memory.

Reports are kept in memory, one per user (the latest), and expire after a
few minutes, so nothing about opponents is stored.
"""

from __future__ import annotations

import itertools
import threading
import time
from typing import Any, Callable

REPORT_TTL_SECONDS = 600
MAX_USERS = 1000
# Reports from the watcher built into the desktop app (same PC, no login of its
# own) go here and are shown to whoever is logged in on that PC. No HTTP route
# can write to this key: the web routes always use a username (or "local").
SCREEN_KEY = "*screen*"
# The watcher reports its state every few seconds; quieter than this means it
# isn't running, so the page never shows "connected" for a watcher that died.
STATUS_FRESH_SECONDS = 10
# A "read again" request the watcher hasn't picked up by then is dropped.
RESCAN_TTL_SECONDS = 30


class WatchBoard:
    """Latest opponent sighting per user. Thread-safe and bounded."""

    def __init__(self, *, clock: Callable[[], float] = time.time):
        self.clock = clock
        self._latest: dict[str, dict[str, Any]] = {}
        self._status: dict[str, dict[str, Any]] = {}
        self._rescan: dict[str, float] = {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def report(self, user: str, name: str | None, clan: str | None,
               rating: int | None = None, *, kind: str = "opponent") -> dict[str, Any]:
        """kind "opponent": what was read (name may be None if only the clan
        could be read). "unreadable": in a battle, nothing in the opponent's
        corner could be read. "ended": that unreadable battle is over."""
        entry = {"id": next(self._ids), "kind": kind, "name": name, "clan": clan,
                 "rating": rating, "at": self.clock()}
        with self._lock:
            if len(self._latest) >= MAX_USERS and user not in self._latest:
                self._latest.clear()  # bounded memory: forget everyone rather than grow
            self._latest[user] = entry
        return entry

    def latest(self, user: str) -> dict[str, Any] | None:
        """The newest report for this user (including the desktop app's own)."""
        with self._lock:
            entries = [e for e in (self._latest.get(user), self._latest.get(SCREEN_KEY)) if e]
        entries = [e for e in entries if self.clock() - e["at"] <= REPORT_TTL_SECONDS]
        if not entries:
            return None
        entry = max(entries, key=lambda e: e["id"])
        return {**entry, "age_seconds": round(self.clock() - entry["at"], 1)}

    def request_rescan(self, user: str) -> None:
        """The page asks the watcher to read this battle's opponent again.
        Both this user's own watcher and the desktop app's are asked."""
        with self._lock:
            if len(self._rescan) >= MAX_USERS and user not in self._rescan:
                self._rescan.clear()
            for key in (user, SCREEN_KEY):
                self._rescan[key] = self.clock()

    def take_rescan(self, user: str) -> bool:
        """True once per request (if still fresh) for the watcher reporting as user."""
        with self._lock:
            at = self._rescan.pop(user, None)
        return at is not None and self.clock() - at <= RESCAN_TTL_SECONDS

    def set_status(self, user: str, state: str, detail: str | None = None) -> None:
        """What the watcher can see right now (connected, no window, ...)."""
        with self._lock:
            if len(self._status) >= MAX_USERS and user not in self._status:
                self._status.clear()
            self._status[user] = {"state": state, "detail": detail, "at": self.clock()}

    def status(self, user: str) -> dict[str, Any] | None:
        """The freshest watcher status for this user, or None if no watcher is running."""
        with self._lock:
            entries = [e for e in (self._status.get(user), self._status.get(SCREEN_KEY)) if e]
        fresh = [e for e in entries if self.clock() - e["at"] <= STATUS_FRESH_SECONDS]
        if not fresh:
            return None
        entry = max(fresh, key=lambda e: e["at"])
        return {"state": entry["state"], "detail": entry["detail"]}
