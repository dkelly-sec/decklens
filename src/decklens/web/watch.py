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
        with self._lock:
            entry = self._latest.get(user)
        if entry is None or self.clock() - entry["at"] > REPORT_TTL_SECONDS:
            return None
        return {**entry, "age_seconds": round(self.clock() - entry["at"], 1)}
