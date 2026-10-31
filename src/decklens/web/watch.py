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


class WatchBoard:
    """Latest opponent sighting per user. Thread-safe and bounded."""

    def __init__(self, *, clock: Callable[[], float] = time.time):
        self.clock = clock
        self._latest: dict[str, dict[str, Any]] = {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def report(self, user: str, name: str, clan: str | None) -> dict[str, Any]:
        entry = {"id": next(self._ids), "name": name, "clan": clan, "at": self.clock()}
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
