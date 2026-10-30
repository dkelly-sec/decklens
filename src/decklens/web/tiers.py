"""Archetype matchup records by skill tier (top 200 / 1,000 / 10,000).

A battle counts in a tier only if BOTH players were inside it when they played
(their leaderboard rank at battle time), so "top 200" really means games
between top-200 players. Exact-deck matchups almost never repeat, so records
are kept per archetype pair, using the trained archetype model to label decks.

Tables are rebuilt from the database at most every 10 minutes and shared by
all requests.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Sequence

import numpy as np

from ..archetypes import ArchetypeModel
from ..model import wilson_interval

log = logging.getLogger("decklens.web.tiers")

TIERS = (200, 1000, 10_000)
WINDOW_DAYS = 14

_ROWS = """
    SELECT b.winner_side, p0.card_ids, p0.global_rank, p1.card_ids, p1.global_rank
    FROM battles b
    JOIN battle_players p0 ON p0.battle_id = b.battle_id AND p0.side = 0
    JOIN battle_players p1 ON p1.battle_id = b.battle_id AND p1.side = 1
    WHERE b.battle_type = 'pathOfLegend'
      AND b.winner_side IS NOT NULL
      AND p0.global_rank IS NOT NULL AND p1.global_rank IS NOT NULL
      AND b.battle_time >= now() - make_interval(days => %s)
"""


def load_rows(conn: Any, days: int = WINDOW_DAYS) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(_ROWS, (days,))
        return cur.fetchall()


@dataclass
class TierTable:
    wins: dict[int, np.ndarray]   # tier -> wins[i, j] (archetype i beat j)
    games: dict[int, np.ndarray]  # tier -> games[i, j]


def build_table(model: ArchetypeModel, rows: Sequence[tuple]) -> TierTable:
    k = len(model.names)
    wins = {t: np.zeros((k, k)) for t in TIERS}
    games = {t: np.zeros((k, k)) for t in TIERS}
    if not rows:
        return TierTable(wins, games)
    side0 = model.assign([tuple(r[1]) for r in rows])
    side1 = model.assign([tuple(r[3]) for r in rows])
    for (winner, _, rank0, _, rank1), i, j in zip(rows, side0, side1):
        worst = max(rank0, rank1)
        winning, losing = (i, j) if winner == 0 else (j, i)
        for t in TIERS:
            if worst <= t:
                wins[t][winning, losing] += 1
                games[t][i, j] += 1
                if i != j:
                    games[t][j, i] += 1
    return TierTable(wins, games)


class TierStats:
    def __init__(self, model: ArchetypeModel, load: Callable[[], Sequence[tuple]],
                 *, ttl: float = 600.0, clock: Callable[[], float] = time.monotonic):
        self.model, self._load, self.ttl, self.clock = model, load, ttl, clock
        self._table: TierTable | None = None
        self._built_at = -float("inf")
        self._lock = threading.Lock()

    def table(self) -> TierTable | None:
        with self._lock:
            if self._table is None or self.clock() - self._built_at >= self.ttl:
                try:
                    self._table = build_table(self.model, self._load())
                except Exception as exc:  # database trouble: keep serving the last table
                    log.warning("could not refresh tier stats: %s", type(exc).__name__)
                self._built_at = self.clock()
            return self._table

    def matchup(self, i: int, j: int) -> list[dict[str, Any]] | None:
        """One row per tier: wins, games, win rate and 95% range for archetype i vs j."""
        table = self.table()
        if table is None:
            return None
        rows = []
        for t in TIERS:
            n = int(table.games[t][i, j])
            row: dict[str, Any] = {"tier": t, "games": n}
            if i == j:
                row["mirror"] = True
            elif n:
                w = int(table.wins[t][i, j])
                lo, hi = wilson_interval(w, n)
                row.update(wins=w, rate=round(w / n, 3), range=[round(lo, 3), round(hi, 3)])
            rows.append(row)
        return rows
