"""Synthetic ranked battles with known structure, for testing the models.

- Each card has a hidden strength; card 0 is planted as clearly the best.
- Card 1 is a hard counter to card 2.
- Higher card levels help.
- Skill matters: a better leaderboard rank helps. 30% of players are unranked.
- Confound: card 5 is useless, but skilled players (top 1,000) pick it far
  more often. A model that ignores skill will wrongly think card 5 is good.
- Extra random noise, so even a perfect model can't be near 100%.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import numpy as np

from decklens.features import UNRANKED_RANK, Matchup

N_CARDS = 60
BASE = 26000000
STRONG_CARD = BASE + 0
COUNTER, COUNTERED = BASE + 1, BASE + 2
CONFOUND_CARD = BASE + 5


def card_id(i: int) -> int:
    return BASE + i


def make_matchups(n: int = 6000, seed: int = 0, skill_noise: float = 1.0,
                  skill_weight: float = 0.5
                  ) -> tuple[list[Matchup], dict[int, str], dict[int, float]]:
    rng = np.random.default_rng(seed)
    strength = rng.normal(0, 0.25, N_CARDS)
    strength[0] = 1.2   # planted best card
    strength[5] = 0.0   # the confounded card does nothing
    popularity = rng.dirichlet(np.ones(N_CARDS) * 2)
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)

    def player() -> tuple[int | None, tuple[int, ...]]:
        rank = int(math.exp(rng.uniform(0, math.log(10_000)))) if rng.random() < 0.7 else None
        cards = list(rng.choice(N_CARDS, 8, replace=False, p=popularity))
        skilled = rank is not None and rank <= 1000
        if skilled and 5 not in cards and rng.random() < 0.8:
            cards[int(rng.integers(8))] = 5
        return rank, tuple(sorted(card_id(int(i)) for i in cards))

    def skill(rank: int | None) -> float:
        return -math.log(rank if rank is not None else UNRANKED_RANK)

    matchups = []
    for k in range(n):
        (rank_a, a), (rank_b, b) = player(), player()
        gap_a, gap_b = rng.uniform(0, 2, 2)
        logit = sum(strength[c - BASE] for c in a) - sum(strength[c - BASE] for c in b)
        logit += 0.6 * (gap_b - gap_a)
        logit += skill_weight * (skill(rank_a) - skill(rank_b))
        if COUNTER in a and COUNTERED in b:
            logit += 1.5
        if COUNTER in b and COUNTERED in a:
            logit -= 1.5
        logit += rng.normal(0, skill_noise)
        won = int(rng.random() < 1 / (1 + math.exp(-logit)))
        matchups.append(Matchup(f"b{k:06d}", start + timedelta(minutes=k), a, b,
                                float(gap_a), float(gap_b), won, rank_a, rank_b))

    names = {card_id(i): f"Card {i}" for i in range(N_CARDS)}
    return matchups, names, {card_id(i): float(s) for i, s in enumerate(strength)}


# --- Archetypes -----------------------------------------------------------------
#
# Six archetypes, each defined by five core cards plus three random flex cards.
# Two rock-paper-scissors cycles: 0 > 1 > 2 > 0 and 3 > 4 > 5 > 3. No single
# card is strong on its own, so only a model that sees deck types can win.

N_ARCHETYPES = 6
RPS_EDGE = 1.2


def core_cards(archetype: int) -> tuple[int, ...]:
    return tuple(card_id(10 + 5 * archetype + i) for i in range(5))


def _beats(i: int, j: int) -> float:
    if i // 3 != j // 3 or i == j:
        return 0.0
    return RPS_EDGE if (j - i) % 3 == 1 else -RPS_EDGE


def make_archetype_matchups(n: int = 6000, seed: int = 0
                            ) -> tuple[list[Matchup], dict[int, str], dict[tuple, int]]:
    rng = np.random.default_rng(seed)
    flex_pool = [card_id(i) for i in range(40, 60)]
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    truth: dict[tuple, int] = {}

    def deck(archetype: int) -> tuple[int, ...]:
        flex = rng.choice(flex_pool, 3, replace=False)
        d = tuple(sorted(core_cards(archetype) + tuple(int(c) for c in flex)))
        truth[d] = archetype
        return d

    matchups = []
    for k in range(n):
        i, j = (int(x) for x in rng.integers(N_ARCHETYPES, size=2))
        a, b = deck(i), deck(j)
        logit = _beats(i, j) + rng.normal(0, 0.5)
        won = int(rng.random() < 1 / (1 + math.exp(-logit)))
        matchups.append(Matchup(f"r{k:06d}", start + timedelta(minutes=k), a, b, 0.0, 0.0, won))

    names = {card_id(i): f"Card {i}" for i in range(N_CARDS)}
    return matchups, names, truth
