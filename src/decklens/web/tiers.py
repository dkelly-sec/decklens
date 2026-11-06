"""Matchup records for your deck against theirs, by skill tier and by how
closely the decks have to match.

Columns: top 200, top 1,000 and all players. A battle counts in a tier only if
BOTH players were inside it when they played, so "top 200" really means games
between top-200 players.

Rows, from strictest to loosest:
  exact      both decks card-for-card the same as yours and theirs
  7 / 6      each deck shares at least 7 (or 6) of its 8 cards with yours / theirs
  archetype  same deck styles, using the trained archetype model
Every level also needs the same win condition, the same spells and the same
active Evolutions and Heroes on each deck: swapping Fireball for Rocket keeps
7 of 8 cards but can turn an X-Bow mirror into 70-30, and Evo Giant is not
plain Giant, so those games are a different matchup.

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
from datetime import datetime, timezone
from typing import Any, Callable, Sequence

import numpy as np

from ..archetypes import ArchetypeModel, _deck_key
from ..battles import form_tokens, is_form_token
from ..model import wilson_interval
from .services import card_kind

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


# Rank at battle time for each side: the battle's own rank (top 500), else the
# nearest snapshot within a day: the global leaderboard (top 1,000) first, then
# the player's profile read when they were crawled (ranks to about 10,000).
# Snapshots never beat BATTLE_RANK_LIMIT + 1: no battle rank means outside the top
# 500 then. Old-season ratings aren't used: early in a season they'd say 2,800
# when nobody is past 2,200 yet. Written out in full for both sides: no SQL is
# ever assembled from strings.
_ROWS = """
    SELECT b.winner_side, p0.card_ids, r0.rank, p1.card_ids, r1.rank,
           -- just each card's id and evolution flags, in slot order (for Evos and
           -- Heroes): the full card data, art links and all, is many times bigger
           (SELECT jsonb_agg(jsonb_build_object('id', c -> 'id', 'evolutionLevel',
                                                c -> 'evolutionLevel') ORDER BY i)
            FROM jsonb_array_elements(p0.cards) WITH ORDINALITY AS e(c, i)),
           (SELECT jsonb_agg(jsonb_build_object('id', c -> 'id', 'evolutionLevel',
                                                c -> 'evolutionLevel') ORDER BY i)
            FROM jsonb_array_elements(p1.cards) WITH ORDINALITY AS e(c, i)),
           b.battle_time
    FROM battles b
    JOIN battle_players p0 ON p0.battle_id = b.battle_id AND p0.side = 0
    JOIN battle_players p1 ON p1.battle_id = b.battle_id AND p1.side = 1
    CROSS JOIN LATERAL (
        SELECT COALESCE(p0.global_rank, (
            SELECT GREATEST(s.rank, %(limit)s + 1)
            FROM player_rank_snapshots s
            WHERE s.tag = p0.player_tag
              AND s.source IN ('leaderboard', 'profile') AND s.rank IS NOT NULL
              AND s.observed_at BETWEEN b.battle_time - make_interval(hours => %(hours)s)
                                    AND b.battle_time + make_interval(hours => %(hours)s)
            ORDER BY s.source = 'profile', abs(extract(epoch FROM s.observed_at - b.battle_time))
            LIMIT 1
        )) AS rank
    ) r0
    CROSS JOIN LATERAL (
        SELECT COALESCE(p1.global_rank, (
            SELECT GREATEST(s.rank, %(limit)s + 1)
            FROM player_rank_snapshots s
            WHERE s.tag = p1.player_tag
              AND s.source IN ('leaderboard', 'profile') AND s.rank IS NOT NULL
              AND s.observed_at BETWEEN b.battle_time - make_interval(hours => %(hours)s)
                                    AND b.battle_time + make_interval(hours => %(hours)s)
            ORDER BY s.source = 'profile', abs(extract(epoch FROM s.observed_at - b.battle_time))
            LIMIT 1
        )) AS rank
    ) r1
    WHERE b.battle_type = 'pathOfLegend'
      AND b.winner_side IS NOT NULL
      AND b.battle_time >= now() - make_interval(days => %(days)s)
"""


def load_rows(conn: Any, days: int = WINDOW_DAYS) -> list[tuple]:
    """(winner side, deck 0, rank 0, deck 1, rank 1, battle time) for recent decided
    ranked battles. A rank is None when it isn't known. Decks are card ids then
    form tokens."""
    with conn.cursor() as cur:
        cur.execute(_ROWS, {"days": days, "hours": SNAPSHOT_WINDOW_HOURS,
                            "limit": BATTLE_RANK_LIMIT})
        return [(w, tuple(d0) + form_tokens(c0), r0, tuple(d1) + form_tokens(c1), r1, t)
                for w, d0, r0, d1, r1, c0, c1, t in cur.fetchall()]


@dataclass
class BattleTable:
    columns: dict[int, int]   # card id -> matrix column
    cards0: np.ndarray        # (battles, cards) 1 where side 0 played that card
    cards1: np.ndarray
    winner: np.ndarray        # 0 or 1
    worst: np.ndarray         # worse of the two ranks; inf when either is unknown
    arch0: np.ndarray         # archetype of each side
    arch1: np.ndarray
    decks0: list[tuple[int, ...]]  # each side's deck, for the meta and the tweak finder
    decks1: list[tuple[int, ...]]
    days_ago: np.ndarray      # whole days before the table was built; -1 when unknown
    rank0: np.ndarray         # each side's rank at battle time; inf when unknown
    rank1: np.ndarray
    lock0: np.ndarray         # each side's win condition and spells, as an id (see lock_of)
    lock1: np.ndarray
    locks: dict[tuple, int]   # win condition and spells -> id

    def lock_id(self, deck: Sequence[int], wincons: Sequence[int]) -> int:
        return self.locks.get(lock_of(deck, wincons), -1)  # -1: no stored deck has it

    def overlap(self, side: np.ndarray, deck: Sequence[int]) -> np.ndarray:
        """How many of deck's cards each battle's side played (plain cards; forms go in the lock)."""
        cols = [self.columns[c] for c in set(deck) if c in self.columns and not is_form_token(c)]
        return side[:, cols].sum(axis=1) if cols else np.zeros(len(side), dtype=int)


def lock_of(deck: Sequence[int], wincons: Sequence[int]) -> tuple:
    """What must stay the same for decks to count as similar: win condition, spells,
    and which cards are played as Evolutions or Heroes."""
    # shortcut: a deck with a hidden Hero is keyed on its 7 known cards, so if that Hero is its
    # win condition it finds no similar games; fix if hero win conditions get common.
    return (_deck_key(deck, wincons), tuple(sorted(c for c in set(deck) if card_kind(c) == "spell")),
            tuple(sorted(c for c in set(deck) if is_form_token(c))))


def _per_deck(model: ArchetypeModel, decks: list[tuple[int, ...]], locks: dict[tuple, int]
              ) -> tuple[np.ndarray, np.ndarray]:
    """(archetype, lock id) for each deck. Top-ladder decks repeat a lot, so each
    distinct deck is worked out once."""
    unique = list(dict.fromkeys(decks))
    if not unique:
        return np.zeros(0, dtype=int), np.zeros(0, dtype=int)
    where = {d: i for i, d in enumerate(unique)}
    index = np.array([where[d] for d in decks], dtype=int)
    archs = model.assign(unique)
    lock = np.array([locks.setdefault(lock_of(d, model._wincons), len(locks)) for d in unique],
                    dtype=int)
    return archs[index], lock[index]


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
    ranks = [np.array([r[i] if r[i] is not None else np.inf for r in rows], dtype=float)
             for i in (2, 4)]
    worst = np.maximum(ranks[0], ranks[1])
    now = datetime.now(timezone.utc)
    days_ago = np.array([(now - r[5]).days if len(r) > 5 and r[5] else -1 for r in rows],
                        dtype=int)  # older 5-field rows carry no time
    locks: dict[tuple, int] = {}
    arch0, lock0 = _per_deck(model, [tuple(r[1]) for r in rows], locks)
    arch1, lock1 = _per_deck(model, [tuple(r[3]) for r in rows], locks)
    return BattleTable(
        columns, cards0, cards1,
        winner=np.array([int(r[0]) for r in rows], dtype=int),
        worst=worst,
        arch0=arch0, arch1=arch1,
        decks0=[tuple(r[1]) for r in rows], decks1=[tuple(r[3]) for r in rows],
        days_ago=days_ago, rank0=ranks[0], rank1=ranks[1],
        lock0=lock0, lock1=lock1,
        locks=locks,
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


FIRST_BUILD_WAIT = 180.0   # seconds a request waits for the very first table


class TierStats:
    """The battle table, rebuilt every ttl seconds. With background=True (the app),
    a rebuild runs on its own thread while requests keep getting the previous
    table, so no request waits for the database; only the first build is waited
    for, and warm() starts that one when the app starts."""

    def __init__(self, model: ArchetypeModel, load: Callable[[], Sequence[tuple]],
                 *, ttl: float = 600.0, clock: Callable[[], float] = time.monotonic,
                 background: bool = False):
        self.model, self._load, self.ttl, self.clock = model, load, ttl, clock
        self.background = background
        self._table: BattleTable | None = None
        self._built_at = -float("inf")
        self._lock = threading.Lock()
        self._building = False
        self._first = threading.Event()

    def _rebuild(self) -> None:
        try:
            table: BattleTable | None = build_table(self.model, self._load())
        except Exception as exc:  # database trouble: keep serving the last table
            log.warning("could not refresh matchup stats: %s", type(exc).__name__)
            table = None
        with self._lock:
            if table is not None:
                self._table = table
            self._built_at = self.clock()
            self._building = False
        self._first.set()

    def warm(self) -> None:
        """Start building now (in the background), before anyone asks."""
        self.table(wait=False)

    def table(self, wait: bool = True) -> BattleTable | None:
        with self._lock:
            due = self._table is None or self.clock() - self._built_at >= self.ttl
            start = due and not self._building
            if start:
                self._building = True
        if start and not self.background:
            self._rebuild()
        elif start:
            threading.Thread(target=self._rebuild, name="battle-table", daemon=True).start()
        if self._table is None and wait and self.background:
            self._first.wait(FIRST_BUILD_WAIT)
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
        wincons = self.model._wincons
        la, lb = t.lock_id(deck_a, wincons), t.lock_id(deck_b, wincons)
        locked_first = (t.lock0 == la) & (t.lock1 == lb)
        locked_second = (t.lock1 == la) & (t.lock0 == lb)
        levels = []
        for key, need in LEVELS:
            if need is None:
                first = (t.arch0 == arch_a) & (t.arch1 == arch_b)    # you were side 0
                second = (t.arch1 == arch_a) & (t.arch0 == arch_b)   # you were side 1
                mirror = arch_a == arch_b
            else:
                need_a = need - (DECK_SIZE - sum(not is_form_token(c) for c in set(deck_a)))
                need_b = need - (DECK_SIZE - sum(not is_form_token(c) for c in set(deck_b)))
                first = (a0 >= need_a) & (b1 >= need_b)
                second = (a1 >= need_a) & (b0 >= need_b)
                mirror = deck_mirror
            first, second = first & locked_first, second & locked_second
            games = first | second
            won = (first & (t.winner == 0)) | (~first & second & (t.winner == 1))
            levels.append({"level": key, "cells": [
                {"tier": c, **_cell(won, games & m, mirror)} for c, m in zip(COLUMNS, tier_masks)
            ]})
        return {"window_days": WINDOW_DAYS, "levels": levels}
