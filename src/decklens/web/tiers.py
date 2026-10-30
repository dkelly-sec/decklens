"""Archetype matchup records by skill tier (top 200 / 1,000 / 10,000).

A battle counts in a tier only if BOTH players were inside it when they played,
so "top 200" really means games between top-200 players.

Where the rank comes from: battle logs only carry a rank for the top 500
(checked against real data: 9,698 ranked sides, highest rank 500). Beyond
that, the player's rank is taken from the nightly leaderboard snapshot
nearest the battle, within SNAPSHOT_WINDOW_HOURS. A player with no battle rank
was outside the top 500 when they played, so a snapshot saying otherwise is
clamped to 501 rather than trusted. Exact-deck matchups almost never repeat, so records
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
BATTLE_RANK_LIMIT = 500       # the API only puts a rank on battles for the top 500
SNAPSHOT_WINDOW_HOURS = 24    # leaderboard snapshots are taken nightly


# Rank at battle time for each side: the battle's own rank, else the nearest
# leaderboard snapshot (never better than BATTLE_RANK_LIMIT + 1). Written out in
# full for both sides: no SQL is ever assembled from strings.
_ROWS = """
    SELECT b.winner_side, p0.card_ids, r0.rank, p1.card_ids, r1.rank
    FROM battles b
    JOIN battle_players p0 ON p0.battle_id = b.battle_id AND p0.side = 0
    JOIN battle_players p1 ON p1.battle_id = b.battle_id AND p1.side = 1
    CROSS JOIN LATERAL (
        SELECT COALESCE(p0.global_rank, (
            SELECT GREATEST(s.rank, %(limit)s + 1)
            FROM player_rank_snapshots s
            WHERE s.tag = p0.player_tag
              AND s.source = 'leaderboard' AND s.rank IS NOT NULL
              AND s.observed_at BETWEEN b.battle_time - make_interval(hours => %(hours)s)
                                    AND b.battle_time + make_interval(hours => %(hours)s)
            ORDER BY abs(extract(epoch FROM s.observed_at - b.battle_time))
            LIMIT 1
        )) AS rank
    ) r0
    CROSS JOIN LATERAL (
        SELECT COALESCE(p1.global_rank, (
            SELECT GREATEST(s.rank, %(limit)s + 1)
            FROM player_rank_snapshots s
            WHERE s.tag = p1.player_tag
              AND s.source = 'leaderboard' AND s.rank IS NOT NULL
              AND s.observed_at BETWEEN b.battle_time - make_interval(hours => %(hours)s)
                                    AND b.battle_time + make_interval(hours => %(hours)s)
            ORDER BY abs(extract(epoch FROM s.observed_at - b.battle_time))
            LIMIT 1
        )) AS rank
    ) r1
    WHERE b.battle_type = 'pathOfLegend'
      AND b.winner_side IS NOT NULL
      AND b.battle_time >= now() - make_interval(days => %(days)s)
      AND r0.rank IS NOT NULL AND r1.rank IS NOT NULL
"""


def load_rows(conn: Any, days: int = WINDOW_DAYS) -> list[tuple]:
    """(winner side, deck 0, rank 0, deck 1, rank 1) for recent ranked battles
    where both players' ranks are known."""
    with conn.cursor() as cur:
        cur.execute(_ROWS, {"days": days, "hours": SNAPSHOT_WINDOW_HOURS,
                            "limit": BATTLE_RANK_LIMIT})
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
