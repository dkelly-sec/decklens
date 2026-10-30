"""Matchup records for your deck against theirs, by skill tier and by how
closely the decks have to match.

Columns: top 200, top 1,000 and all players. A battle counts in a tier only if
BOTH players were inside it when they played, so "top 200" really means games
between top-200 players.

Rows, from strictest to loosest:
  exact      both decks card-for-card the same as yours and theirs
  7 / 6      each deck shares at least 7 (or 6) of its 8 cards with yours / theirs
  archetype  same deck styles, using the trained archetype model

Where ranks come from: battle logs only carry a rank for the top 500 (checked
against real data: 9,698 ranked sides, highest rank 500). Beyond that, the
player's rank is taken from the nightly leaderboard snapshot nearest the
battle, within SNAPSHOT_WINDOW_HOURS. A player with no battle rank was outside
the top 500 when they played, so a snapshot saying otherwise is clamped to 501
rather than trusted. Tiers stop at the top 1,000.

Battles are reloaded from the database at most every 10 minutes and kept as
card matrices, so each request is a few vectorised numpy operations.
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

TIERS = (200, 1000)
COLUMNS: tuple[int | None, ...] = (*TIERS, None)   # None = all players
DECK_SIZE = 8
# (key, cards that must match out of 8, or None for the archetype level)
LEVELS: tuple[tuple[str, int | None], ...] = (
    ("exact", 8), ("7", 7), ("6", 6), ("archetype", None),
)
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
"""


def load_rows(conn: Any, days: int = WINDOW_DAYS) -> list[tuple]:
    """(winner side, deck 0, rank 0, deck 1, rank 1) for recent decided ranked
    battles. A rank is None when it isn't known."""
    with conn.cursor() as cur:
        cur.execute(_ROWS, {"days": days, "hours": SNAPSHOT_WINDOW_HOURS,
                            "limit": BATTLE_RANK_LIMIT})
        return cur.fetchall()


@dataclass
class BattleTable:
    columns: dict[int, int]   # card id -> matrix column
    cards0: np.ndarray        # (battles, cards) 1 where side 0 played that card
    cards1: np.ndarray
    winner: np.ndarray        # 0 or 1
    worst: np.ndarray         # worse of the two ranks; inf when either is unknown
    arch0: np.ndarray         # archetype of each side
    arch1: np.ndarray

    def overlap(self, side: np.ndarray, deck: Sequence[int]) -> np.ndarray:
        """How many of deck's cards each battle's side played."""
        cols = [self.columns[c] for c in set(deck) if c in self.columns]
        return side[:, cols].sum(axis=1) if cols else np.zeros(len(side), dtype=int)


def build_table(model: ArchetypeModel, rows: Sequence[tuple]) -> BattleTable:
    columns: dict[int, int] = {}
    for r in rows:
        for c in (*r[1], *r[3]):
            columns.setdefault(int(c), len(columns))
    n, k = len(rows), max(len(columns), 1)
    cards0 = np.zeros((n, k), dtype=np.int16)
    cards1 = np.zeros((n, k), dtype=np.int16)
    for row, r in enumerate(rows):
        cards0[row, [columns[int(c)] for c in r[1]]] = 1
        cards1[row, [columns[int(c)] for c in r[3]]] = 1
    worst = np.array([max(r[2], r[4]) if r[2] is not None and r[4] is not None else np.inf
                      for r in rows], dtype=float)
    empty = np.zeros(0, dtype=int)
    return BattleTable(
        columns, cards0, cards1,
        winner=np.array([int(r[0]) for r in rows], dtype=int),
        worst=worst,
        arch0=model.assign([tuple(r[1]) for r in rows]) if n else empty,
        arch1=model.assign([tuple(r[3]) for r in rows]) if n else empty,
    )


def _cell(won: np.ndarray, games: np.ndarray, mirror: bool) -> dict[str, Any]:
    n = int(games.sum())
    cell: dict[str, Any] = {"games": n}
    if mirror:
        cell["mirror"] = True
    elif n:
        w = int((won & games).sum())
        lo, hi = wilson_interval(w, n)
        cell.update(wins=w, rate=round(w / n, 3), range=[round(lo, 3), round(hi, 3)])
    return cell


class TierStats:
    def __init__(self, model: ArchetypeModel, load: Callable[[], Sequence[tuple]],
                 *, ttl: float = 600.0, clock: Callable[[], float] = time.monotonic):
        self.model, self._load, self.ttl, self.clock = model, load, ttl, clock
        self._table: BattleTable | None = None
        self._built_at = -float("inf")
        self._lock = threading.Lock()

    def table(self) -> BattleTable | None:
        with self._lock:
            if self._table is None or self.clock() - self._built_at >= self.ttl:
                try:
                    self._table = build_table(self.model, self._load())
                except Exception as exc:  # database trouble: keep serving the last table
                    log.warning("could not refresh matchup stats: %s", type(exc).__name__)
                self._built_at = self.clock()
            return self._table

    def matchup(self, deck_a: Sequence[int], deck_b: Sequence[int], arch_a: int, arch_b: int
                ) -> dict[str, Any] | None:
        """Your record (deck_a) against theirs (deck_b) at every match level and tier.

        A 7-card deck (a Hero the API hides) needs one card fewer to match.
        """
        t = self.table()
        if t is None:
            return None
        deck_mirror = set(deck_a) == set(deck_b)
        tier_masks = [t.worst <= c if c is not None else np.ones(len(t.worst), dtype=bool)
                      for c in COLUMNS]
        a0, a1 = t.overlap(t.cards0, deck_a), t.overlap(t.cards1, deck_a)
        b0, b1 = t.overlap(t.cards0, deck_b), t.overlap(t.cards1, deck_b)
        levels = []
        for key, need in LEVELS:
            if need is None:
                first = (t.arch0 == arch_a) & (t.arch1 == arch_b)    # you were side 0
                second = (t.arch1 == arch_a) & (t.arch0 == arch_b)   # you were side 1
                mirror = arch_a == arch_b
            else:
                need_a = need - (DECK_SIZE - len(set(deck_a)))
                need_b = need - (DECK_SIZE - len(set(deck_b)))
                first = (a0 >= need_a) & (b1 >= need_b)
                second = (a1 >= need_a) & (b0 >= need_b)
                mirror = deck_mirror
            games = first | second
            won = (first & (t.winner == 0)) | (~first & second & (t.winner == 1))
            levels.append({"level": key, "cells": [
                {"tier": c, **_cell(won, games & m, mirror)} for c, m in zip(COLUMNS, tier_masks)
            ]})
        return {"window_days": WINDOW_DAYS, "levels": levels}
