"""Turn matchups into numbers a model can learn from.

A matchup is deck A against deck B, labelled 1 if A won. Player identity is
never a feature, so the model can't learn "this player usually wins". It does
see how skilled each side was (leaderboard rank at battle time), so card
effects are measured between equally skilled players instead of absorbing
"good players pick these cards".

Two encodings, each ending with the same extra features:
- difference: +1 for each card in A, -1 for each card in B. A linear model on
  this learns one strength per card, and swapping the decks exactly flips the
  prediction. Simple, explainable baseline.
- pair: A's cards and B's cards side by side, so a tree model can learn
  counters (card X does well against card Y).

Extra features, all "A minus B", so swapping sides flips their sign:
- level: card-level advantage (average levels below max)
- skill: log of rank ratio; ranked 100th vs 1,000th is +2.3 for the 100th
- ranked: whether each side had a leaderboard rank at all
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Iterable, Sequence

import numpy as np

EXTRA_FEATURES = ("level", "skill", "ranked")
UNRANKED_RANK = 15_000  # anyone without a top-10,000 rank is treated as about here


@dataclass(frozen=True)
class Matchup:
    battle_id: str
    battle_time: datetime
    deck_a: tuple[int, ...]
    deck_b: tuple[int, ...]
    level_gap_a: float  # average levels below max in A's deck (0 = maxed)
    level_gap_b: float
    a_won: int  # 1 if deck A won, else 0
    rank_a: int | None = None  # leaderboard rank at battle time, if top 10,000
    rank_b: int | None = None


def level_gap(cards: Iterable[Any]) -> float:
    """Average number of levels each card is below its max (0 for a maxed deck)."""
    gaps = []
    for card in cards:
        if not isinstance(card, dict):
            continue
        level, max_level = card.get("level"), card.get("maxLevel")
        if isinstance(level, int) and isinstance(max_level, int) and max_level >= level:
            gaps.append(max_level - level)
    return float(sum(gaps)) / len(gaps) if gaps else 0.0


def _valid_rank(rank: object) -> bool:
    return isinstance(rank, int) and not isinstance(rank, bool) and 1 <= rank <= UNRANKED_RANK


def _effective_rank(rank: object) -> int:
    return rank if _valid_rank(rank) else UNRANKED_RANK  # type: ignore[return-value]


def extra_features(m: Matchup) -> tuple[float, float, float]:
    """(level advantage, skill advantage, ranked difference) for side A."""
    level = m.level_gap_b - m.level_gap_a
    skill = math.log(_effective_rank(m.rank_b)) - math.log(_effective_rank(m.rank_a))
    ranked = float(_valid_rank(m.rank_a)) - float(_valid_rank(m.rank_b))
    return level, skill, ranked


class CardVocab:
    """Maps card ids to feature columns. Built from training data only."""

    def __init__(self, card_ids: Sequence[int], names: dict[int, str] | None = None):
        self.card_ids = [int(c) for c in card_ids]  # plain ints, whatever the source
        self.index = {cid: i for i, cid in enumerate(self.card_ids)}
        self.names = {int(k): str(v) for k, v in (names or {}).items()}

    def __len__(self) -> int:
        return len(self.card_ids)

    @classmethod
    def build(
        cls,
        matchups: Iterable[Matchup],
        min_count: int = 1,
        names: dict[int, str] | None = None,
    ) -> "CardVocab":
        counts: Counter = Counter()
        for m in matchups:
            counts.update(m.deck_a)
            counts.update(m.deck_b)
        keep = sorted(cid for cid, n in counts.items() if n >= min_count)
        return cls(keep, names)

    def name(self, card_id: int) -> str:
        return self.names.get(card_id, str(card_id))

    def to_dict(self) -> dict[str, Any]:
        return {"card_ids": self.card_ids, "names": {str(k): v for k, v in self.names.items()}}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CardVocab":
        ids = [int(c) for c in data["card_ids"]]
        names = {int(k): str(v) for k, v in data.get("names", {}).items()}
        return cls(ids, names)

    def one_hot(self, deck: Iterable[int]) -> np.ndarray:
        row = np.zeros(len(self), dtype=np.float32)
        for cid in deck:
            col = self.index.get(cid)
            if col is not None:  # cards unseen in training are ignored
                row[col] = 1.0
        return row


def encode_extras(matchups: Sequence[Matchup]) -> np.ndarray:
    return np.array([extra_features(m) for m in matchups], dtype=np.float32).reshape(
        len(matchups), len(EXTRA_FEATURES))


def encode_difference(vocab: CardVocab, matchups: Sequence[Matchup]) -> np.ndarray:
    """Rows of [A cards - B cards, level, skill, ranked]."""
    n = len(vocab)
    out = np.zeros((len(matchups), n + len(EXTRA_FEATURES)), dtype=np.float32)
    for i, m in enumerate(matchups):
        out[i, :n] = vocab.one_hot(m.deck_a) - vocab.one_hot(m.deck_b)
    out[:, n:] = encode_extras(matchups)
    return out


def encode_pair(vocab: CardVocab, matchups: Sequence[Matchup]) -> np.ndarray:
    """Rows of [A cards, B cards, level, skill, ranked]."""
    n = len(vocab)
    out = np.zeros((len(matchups), 2 * n + len(EXTRA_FEATURES)), dtype=np.float32)
    for i, m in enumerate(matchups):
        out[i, :n] = vocab.one_hot(m.deck_a)
        out[i, n:2 * n] = vocab.one_hot(m.deck_b)
    out[:, 2 * n:] = encode_extras(matchups)
    return out


def flipped(m: Matchup) -> Matchup:
    """The same battle seen from the other side."""
    return replace(m, deck_a=m.deck_b, deck_b=m.deck_a, level_gap_a=m.level_gap_b,
                   level_gap_b=m.level_gap_a, rank_a=m.rank_b, rank_b=m.rank_a,
                   a_won=1 - m.a_won)


def labels(matchups: Sequence[Matchup]) -> np.ndarray:
    return np.array([m.a_won for m in matchups], dtype=np.int64)
