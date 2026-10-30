"""Deck archetypes and an archetype-vs-archetype matchup model.

Archetypes are found from the data rather than a hand-kept list, so they
follow the meta: decks are grouped by the cards they share (k-means on
normalised card vectors, so it compares decks by overlap), and each group is
named after its signature cards.

Matchups are a table of wins between archetypes. Pairings with few games are
pulled towards a prior from each archetype's overall strength, so a 3-0
record doesn't read as 100%. Both the number of archetypes and how strongly
the table is pulled towards the prior are chosen on battles later than the
ones used to fit, the same way the model is used for real.

Exported as plain JSON (centroids, names, win counts), never pickle.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
from sklearn.cluster import KMeans

from .features import CardVocab, Matchup, labels

ARCHETYPE_FORMAT = "decklens-archetypes-v1"
_EPS = 1e-6


def _deck_matrix(vocab: CardVocab, decks: Sequence[Sequence[int]]) -> np.ndarray:
    X = np.stack([vocab.one_hot(d) for d in decks]) if decks else np.zeros((0, len(vocab)))
    norms = np.linalg.norm(X, axis=1, keepdims=True)
    return X / np.maximum(norms, _EPS)


CORE_SHARE = 0.5      # a card is "core" to a group if at least half its decks run it
MERGE_JACCARD = 0.6   # groups whose cores overlap this much are one archetype


def _card_share(vocab: CardVocab, member_decks: Counter) -> np.ndarray:
    """Fraction of a group's decks that contain each card."""
    share = np.zeros(len(vocab))
    for deck, count in member_decks.items():
        share += vocab.one_hot(deck) * count
    return share / max(sum(member_decks.values()), 1)


def _merge_by_core(cores: list[set[int]]) -> np.ndarray:
    """Group clusters whose core cards mostly overlap (union-find on Jaccard similarity)."""
    parent = list(range(len(cores)))

    def root(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(len(cores)):
        for j in range(i + 1, len(cores)):
            union = cores[i] | cores[j]
            if union and len(cores[i] & cores[j]) / len(union) >= MERGE_JACCARD:
                parent[root(j)] = root(i)
    roots = [root(i) for i in range(len(cores))]
    order = list(dict.fromkeys(roots))  # first-seen order
    return np.array([order.index(r) for r in roots], dtype=int)


def _signature_name(vocab: CardVocab, share: np.ndarray, global_share: np.ndarray) -> str:
    """Name a group after cards common in it but not everywhere."""
    lift = share * (share / np.maximum(global_share, _EPS))
    order = [i for i in np.argsort(-lift) if share[i] >= CORE_SHARE][:3]
    if not order:
        order = [int(np.argmax(share))]
    return " · ".join(vocab.name(vocab.card_ids[i]) for i in order)


@dataclass
class ArchetypeModel:
    vocab: CardVocab
    centroids: np.ndarray       # clusters x cards, unit length
    groups: np.ndarray          # cluster -> archetype (clusters with the same signature merge)
    names: list[str]            # one per archetype, all different
    wins: np.ndarray            # wins[i, j]: battles archetype i won against j
    games: np.ndarray           # games[i, j] == games[j, i]
    prior_strength: float
    deck_counts: np.ndarray     # how many training decks fell in each archetype

    # --- fitting -------------------------------------------------------------

    @classmethod
    def fit(cls, vocab: CardVocab, train: Sequence[Matchup], n_clusters: int,
            prior_strength: float, seed: int = 0) -> "ArchetypeModel":
        counts: Counter = Counter()
        for m in train:
            counts[m.deck_a] += 1
            counts[m.deck_b] += 1
        decks = list(counts)
        k = max(1, min(n_clusters, len(decks)))
        X = _deck_matrix(vocab, decks)
        km = KMeans(n_clusters=k, n_init=4, random_state=seed)
        km.fit(X, sample_weight=np.array([counts[d] for d in decks], dtype=float))
        centroids = km.cluster_centers_ / np.maximum(
            np.linalg.norm(km.cluster_centers_, axis=1, keepdims=True), _EPS)

        members: list[Counter] = [Counter() for _ in range(k)]
        for deck, label in zip(decks, km.labels_):
            members[int(label)][deck] = counts[deck]
        global_share = sum(vocab.one_hot(d) * c for d, c in counts.items()) / sum(counts.values())

        # k-means can split one deck type by its flex cards. Clusters whose core
        # cards mostly overlap are one archetype: merge them, pooling their games.
        cores = [set(np.flatnonzero(_card_share(vocab, members[c]) >= CORE_SHARE))
                 for c in range(k)]
        groups = _merge_by_core(cores)
        m = int(groups.max()) + 1
        merged: list[Counter] = [Counter() for _ in range(m)]
        for c in range(k):
            merged[groups[c]].update(members[c])
        names: list[str] = []
        for g in range(m):
            name = _signature_name(vocab, _card_share(vocab, merged[g]), global_share)
            names.append(name if name not in names else f"{name} ({g + 1})")  # always unique
        deck_counts = np.array([sum(merged[g].values()) for g in range(m)], dtype=float)

        model = cls(vocab, centroids, groups, names, np.zeros((m, m)), np.zeros((m, m)),
                    float(prior_strength), deck_counts)
        model._count(train)
        return model

    def _count(self, matchups: Sequence[Matchup]) -> None:
        a = self.assign([m.deck_a for m in matchups])
        b = self.assign([m.deck_b for m in matchups])
        for i, j, won in zip(a, b, labels(matchups)):
            winner, loser = (i, j) if won else (j, i)
            self.wins[winner, loser] += 1
            self.games[i, j] += 1
            self.games[j, i] += 1

    # --- prediction -----------------------------------------------------------

    def assign(self, decks: Sequence[Sequence[int]]) -> np.ndarray:
        """Nearest archetype for each deck; works for decks never seen in training."""
        if not decks:
            return np.zeros(0, dtype=int)
        nearest = np.argmax(_deck_matrix(self.vocab, decks) @ self.centroids.T, axis=1)
        return self.groups[nearest]

    def _totals(self) -> tuple[np.ndarray, np.ndarray]:
        """Wins and games for each archetype against all others (mirrors excluded)."""
        off = ~np.eye(len(self.names), dtype=bool)
        return (self.wins * off).sum(axis=1), (self.games * off).sum(axis=1)

    def matchup(self, i: int, j: int) -> tuple[float, int]:
        """(chance archetype i beats j, training games between them)."""
        if i == j:
            return 0.5, int(self.games[i, i] / 2)  # mirrors are 50/50 by definition
        won, played = self._totals()
        # Prior from each archetype's record against everyone EXCEPT the other one,
        # so this pairing's own games aren't counted twice. Lightly smoothed.
        w_i, n_i = won[i] - self.wins[i, j], played[i] - self.games[i, j]
        w_j, n_j = won[j] - self.wins[j, i], played[j] - self.games[j, i]
        s_i = math.log((w_i + 5) / (n_i - w_i + 5))
        s_j = math.log((w_j + 5) / (n_j - w_j + 5))
        prior = 1.0 / (1.0 + math.exp(-(s_i - s_j)))
        n, w = self.games[i, j], self.wins[i, j]
        p = (w + self.prior_strength * prior) / (n + self.prior_strength)
        return float(p), int(n)

    def predict_proba(self, matchups: Sequence[Matchup]) -> np.ndarray:
        a = self.assign([m.deck_a for m in matchups])
        b = self.assign([m.deck_b for m in matchups])
        return np.array([self.matchup(int(i), int(j))[0] for i, j in zip(a, b)])

    # --- summaries for the report ---------------------------------------------------

    def summary(self) -> list[dict[str, Any]]:
        """Per archetype: name, share of decks, games and wins against other archetypes."""
        k = len(self.names)
        off = ~np.eye(k, dtype=bool)
        total = self.deck_counts.sum()
        return [
            {"index": i, "name": self.names[i], "share": float(self.deck_counts[i] / total),
             "games": int((self.games[i] * off[i]).sum()),
             "wins": int((self.wins[i] * off[i]).sum())}
            for i in range(k)
        ]

    def matchup_rows(self, min_games: int = 30) -> list[dict[str, Any]]:
        """Every non-mirror pairing with enough games, once (i < j)."""
        rows = []
        for i in range(len(self.names)):
            for j in range(i + 1, len(self.names)):
                n = int(self.games[i, j])
                if n >= min_games:
                    rows.append({"a": self.names[i], "b": self.names[j], "games": n,
                                 "a_wins": int(self.wins[i, j]),
                                 "estimate": self.matchup(i, j)[0]})
        return rows

    # --- safe export ---------------------------------------------------------------

    def to_json(self) -> str:
        return json.dumps({
            "format": ARCHETYPE_FORMAT,
            "vocab": self.vocab.to_dict(),
            "names": self.names,
            "groups": self.groups.astype(int).tolist(),
            "centroids": np.round(self.centroids, 5).tolist(),
            "wins": self.wins.astype(int).tolist(),
            "games": self.games.astype(int).tolist(),
            "deck_counts": self.deck_counts.astype(int).tolist(),
            "prior_strength": self.prior_strength,
        })

    @classmethod
    def from_json(cls, text: str) -> "ArchetypeModel":
        data = json.loads(text)
        if data.get("format") != ARCHETYPE_FORMAT:
            raise ValueError("not a DeckLens archetype model file")
        vocab = CardVocab.from_dict(data["vocab"])
        centroids = np.asarray(data["centroids"], dtype=np.float64)
        wins = np.asarray(data["wins"], dtype=np.float64)
        games = np.asarray(data["games"], dtype=np.float64)
        groups = np.asarray(data["groups"], dtype=int)
        m = len(data["names"])
        if (centroids.ndim != 2 or centroids.shape[1] != len(vocab)
                or groups.shape != (centroids.shape[0],) or wins.shape != (m, m)
                or games.shape != (m, m) or not np.all(np.isfinite(centroids))
                or (groups < 0).any() or (groups >= m).any()
                or (wins < 0).any() or (games < 0).any()):
            raise ValueError("archetype model file is malformed")
        return cls(vocab, centroids, groups, [str(n) for n in data["names"]], wins, games,
                   float(data["prior_strength"]),
                   np.asarray(data["deck_counts"], dtype=np.float64))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8")


def _log_loss(probs: np.ndarray, y: np.ndarray) -> float:
    p = np.clip(probs, _EPS, 1 - _EPS)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def fit_tuned(
    vocab: CardVocab,
    train: Sequence[Matchup],
    cluster_options: Iterable[int] = (15, 30, 60),
    prior_options: Iterable[float] = (10.0, 30.0, 100.0),
    seed: int = 0,
) -> tuple[ArchetypeModel, dict[str, float]]:
    """Pick the number of archetypes and prior strength on the newest 20% of the
    training battles, then refit on all of them."""
    from .model import time_split  # local import: model imports features too

    inner, valid, _ = time_split(train, 0.2)
    y_valid = labels(valid)
    best: tuple[float, int, float] | None = None
    for k in cluster_options:
        model = ArchetypeModel.fit(vocab, inner, k, prior_strength=1.0, seed=seed)
        for prior in prior_options:
            model.prior_strength = float(prior)
            loss = _log_loss(model.predict_proba(valid), y_valid)
            if best is None or loss < best[0]:
                best = (loss, int(k), float(prior))
    if best is None:
        raise ValueError("no archetype or prior options to choose from")
    _, k, prior = best
    return (ArchetypeModel.fit(vocab, train, k, prior, seed=seed),
            {"archetypes": float(k), "prior_strength": prior, "validation_log_loss": best[0]})
