"""Deck archetypes and an archetype-vs-archetype matchup model.

Decks are grouped by win condition first (Golem, X-Bow, Hog Rider...), the
way players name them: a Golem deck is never the same archetype as a Graveyard
deck, however many support cards they share. Within a win condition, decks are
split by the cards they share (k-means on normalised card vectors, so it
compares decks by overlap), which follows the meta without a hand-kept deck
list. Groups are named the way players say them: "Golem Pump", "X-Bow 3.0"
(cycle decks by average elixir), "Graveyard Poison".

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
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
from sklearn.cluster import KMeans

from .battles import FORM_TOKEN
from .features import CardVocab, Matchup, labels

ARCHETYPE_FORMAT = "decklens-archetypes-v2"
OLD_FORMATS = ("decklens-archetypes-v1",)  # no win conditions: still loads, retrain to fix names
_EPS = 1e-6


def _deck_matrix(vocab: CardVocab, decks: Sequence[Sequence[int]]) -> np.ndarray:
    X = np.stack([vocab.one_hot(d) for d in decks]) if decks else np.zeros((0, len(vocab)))
    norms = np.linalg.norm(X, axis=1, keepdims=True)
    return X / np.maximum(norms, _EPS)


# The cards a deck is built to get onto the tower. A deck's archetype family is
# the first of these it runs (Lava Hound before Balloon, so LavaLoon is a Lava
# Hound deck); a second win condition is just another card for k-means to split on.
# shortcut: hand-kept list matched by the API's English card names; add new win conditions as Supercell releases them.
WIN_CONDITIONS = (
    "Golem", "Lava Hound", "Electro Giant", "Goblin Giant", "Minion Giant", "Rune Giant",
    "Elixir Golem",
    "Royal Giant", "Giant", "X-Bow", "Mortar", "Graveyard", "Goblin Barrel", "Hog Rider",
    "Royal Hogs", "Ram Rider", "Battle Ram", "Three Musketeers", "Sparky", "Balloon", "Miner",
    "Goblin Drill", "Skeleton Barrel", "Wall Breakers", "Giant Skeleton", "Royal Recruits",
    "P.E.K.K.A", "Mega Knight",
)
# One, not two: keying on win-condition pairs gave 325 archetypes on 112k battles,
# most of them rare pairs with a handful of games.
KEY_SIZE = 1
SHORT_NAMES = {"Hog Rider": "Hog"}
PUMP = "Elixir Collector"   # players call it pump: "Golem Pump"
CYCLE_MAX_ELIXIR = 3.2      # cycle decks go by average elixir: "Hog 2.6", "X-Bow 3.0"

# A card goes in an archetype's name only if nearly all its decks run it, so the
# name never promises a card the deck you're looking at doesn't have.
NAME_SHARE = 0.8

CORE_SHARE = 0.5      # a card is "core" to a group if at least half its decks run it
MERGE_JACCARD = 0.6   # groups whose cores overlap this much are one archetype


def _card_share(vocab: CardVocab, member_decks: Counter) -> np.ndarray:
    """Fraction of a group's decks that contain each card."""
    share = np.zeros(len(vocab))
    for deck, count in member_decks.items():
        share += vocab.one_hot(deck) * count
    return share / max(sum(member_decks.values()), 1)


def _wincon_ids(vocab: CardVocab) -> list[int]:
    """Win condition card ids, in WIN_CONDITIONS order (names come with the vocab)."""
    by_name = {name: cid for cid, name in vocab.names.items()}
    return [by_name[n] for n in WIN_CONDITIONS if n in by_name]


def _deck_key(deck: Iterable[int], wincons: Sequence[int]) -> tuple[int, ...]:
    present = set(deck)
    return tuple(c for c in wincons if c in present)[:KEY_SIZE]


def _key_name(vocab: CardVocab, key: Sequence[int]) -> str:
    return " + ".join(SHORT_NAMES.get(vocab.name(c), vocab.name(c)) for c in key)


def _avg_elixir(vocab: CardVocab, member_decks: Counter) -> float | None:
    """Average card cost across a group's decks, if costs are known."""
    total = weight = 0.0
    for deck, count in member_decks.items():
        costs = [vocab.elixir[c] for c in deck if c in vocab.elixir]
        if len(costs) >= 6:  # a couple of unknown costs is fine, most unknown isn't
            total += count * sum(costs) / len(costs)
            weight += count
    return total / weight if weight else None


def _merge_by_core(cores: list[set[int]], keys: list[tuple[int, ...]]) -> np.ndarray:
    """Group clusters with the same win conditions whose core cards mostly overlap
    (union-find on Jaccard similarity)."""
    parent = list(range(len(cores)))

    def root(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(len(cores)):
        for j in range(i + 1, len(cores)):
            union = cores[i] | cores[j]
            if keys[i] == keys[j] and union and len(cores[i] & cores[j]) / len(union) >= MERGE_JACCARD:
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


def _archetype_name(vocab: CardVocab, key: tuple[int, ...], share: np.ndarray,
                    global_share: np.ndarray, avg_elixir: float | None
                    ) -> tuple[str, tuple[int, ...]]:
    """('Golem Pump' / 'X-Bow 3.0' / 'Graveyard Poison', the cards the name names);
    signature cards (and no named cards to insist on) without a win condition."""
    if not key:
        return _signature_name(vocab, share, global_share), ()
    head = _key_name(vocab, key)
    core = [vocab.card_ids[i] for i in np.argsort(-share * (share / np.maximum(global_share, _EPS)))
            if share[i] >= NAME_SHARE and vocab.card_ids[i] < FORM_TOKEN["evo"]
            and vocab.card_ids[i] not in key]
    pump = next((c for c in core if vocab.name(c) == PUMP), None)
    if pump is not None:
        return f"{head} Pump", (*key, pump)
    if avg_elixir is not None and avg_elixir <= CYCLE_MAX_ELIXIR:
        return f"{head} {avg_elixir:.1f}", key
    return (f"{head} {vocab.name(core[0])}", (*key, core[0])) if core else (head, key)


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
    keys: list[tuple[int, ...]] = field(default_factory=list)  # cluster -> win conditions ([]: old model)
    named: list[tuple[int, ...]] = field(default_factory=list)  # archetype -> cards its name names

    # --- fitting -------------------------------------------------------------

    @classmethod
    def fit(cls, vocab: CardVocab, train: Sequence[Matchup], n_clusters: int,
            prior_strength: float, seed: int = 0) -> "ArchetypeModel":
        counts: Counter = Counter()
        for m in train:
            counts[m.deck_a] += 1
            counts[m.deck_b] += 1
        wincons = _wincon_ids(vocab)
        buckets: dict[tuple[int, ...], list[tuple[int, ...]]] = {}
        for deck in counts:
            buckets.setdefault(_deck_key(deck, wincons), []).append(deck)

        # Every win condition gets at least one archetype, however rare (a Golem
        # deck must never land in a Graveyard group); common ones get more,
        # in proportion to how often they're played.
        total = sum(counts.values())
        centers, keys, members = [], [], []
        for key, decks in buckets.items():
            weight = np.array([counts[d] for d in decks], dtype=float)
            k = max(1, min(len(decks), round(n_clusters * weight.sum() / total)))
            X = _deck_matrix(vocab, decks)
            if k == 1:
                found, labels_ = (weight @ X)[None, :], np.zeros(len(decks), dtype=int)
            else:
                km = KMeans(n_clusters=k, n_init=4, random_state=seed).fit(X, sample_weight=weight)
                found, labels_ = km.cluster_centers_, km.labels_
            centers.append(found)
            for c in range(k):
                members.append(Counter({d: counts[d] for d, lab in zip(decks, labels_) if lab == c}))
                keys.append(key)
        stacked = np.vstack(centers)
        centroids = stacked / np.maximum(np.linalg.norm(stacked, axis=1, keepdims=True), _EPS)
        k = len(members)
        global_share = sum(vocab.one_hot(d) * c for d, c in counts.items()) / sum(counts.values())

        # k-means can split one deck type by its flex cards. Clusters whose core
        # cards mostly overlap are one archetype: merge them, pooling their games.
        cores = [set(np.flatnonzero(_card_share(vocab, members[c]) >= CORE_SHARE))
                 for c in range(k)]
        groups = _merge_by_core(cores, keys)
        m = int(groups.max()) + 1
        merged: list[Counter] = [Counter() for _ in range(m)]
        group_key: list[tuple[int, ...]] = [()] * m
        for c in range(k):
            merged[groups[c]].update(members[c])
            group_key[groups[c]] = keys[c]
        # Groups that come out with the same name are one archetype as far as a player
        # can tell (say two Elixir Golem groups with no card all their decks share):
        # pool them rather than show "Elixir Golem (2)".
        names: list[str] = []
        named: list[tuple[int, ...]] = []
        renumber = []
        for g in range(m):
            name, cards = _archetype_name(vocab, group_key[g], _card_share(vocab, merged[g]),
                                          global_share, _avg_elixir(vocab, merged[g]))
            if name not in names:
                names.append(name)
                named.append(cards)
            renumber.append(names.index(name))
        pooled: list[Counter] = [Counter() for _ in names]
        for g, n in enumerate(renumber):
            pooled[n].update(merged[g])
        groups = np.array([renumber[g] for g in groups], dtype=int)
        m = len(names)
        deck_counts = np.array([sum(pooled[g].values()) for g in range(m)], dtype=float)

        model = cls(vocab, centroids, groups, names, np.zeros((m, m)), np.zeros((m, m)),
                    float(prior_strength), deck_counts, keys, named)
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
        X = _deck_matrix(self.vocab, decks)
        sims = X @ self.centroids.T
        if any(self.keys):
            masks: dict[tuple[int, ...], np.ndarray] = {}
            rows = []
            for deck in decks:
                key = _deck_key(deck, self._wincons)
                if key not in masks:
                    masks[key] = self._allowed(key)
                rows.append(masks[key])
            allowed = np.stack(rows)
            if self.named:  # "X-Bow Rocket" only for decks with Rocket, when there's a choice
                honest = allowed & self._has_named(X > 0)
                allowed = np.where(honest.any(axis=1, keepdims=True), honest, allowed)
            sims[~allowed] = -np.inf
        return self.groups[np.argmax(sims, axis=1)]

    @cached_property
    def _wincons(self) -> list[int]:
        return _wincon_ids(self.vocab)

    def _allowed(self, key: tuple[int, ...]) -> np.ndarray:
        """Clusters a deck with this key may join: same win conditions, else the
        same main one, else (a win condition training never saw) any."""
        same = np.array([k == key for k in self.keys])
        if not same.any() and key:
            same = np.array([k[:1] == key[:1] for k in self.keys])
        return same if same.any() else np.ones(len(self.keys), dtype=bool)

    def _has_named(self, present: np.ndarray) -> np.ndarray:
        """(decks x clusters): the cluster's archetype name only names cards the deck
        has. present is decks x vocab, True where the deck runs that card."""
        need = np.zeros((len(self.names), len(self.vocab)))
        for g, cards in enumerate(self.named):
            for c in cards:
                if c in self.vocab.index:  # a card too rare for the vocab can't be checked
                    need[g, self.vocab.index[c]] = 1.0
        has = present.astype(float) @ need.T >= need.sum(axis=1)
        return has[:, self.groups]

    def family(self, deck: Sequence[int]) -> str:
        """The deck's win condition ("X-Bow"), or "Other" without one from the list."""
        key = _deck_key(deck, self._wincons)
        return _key_name(self.vocab, key) if key else "Other"

    def labels(self, decks: Sequence[Sequence[int]]) -> list[str]:
        """Archetype name for each deck, but never one naming a card the deck lacks:
        a Skeleton Barrel deck without Suspicious Bush is just "Skeleton Barrel" even
        if every Skeleton Barrel deck in training ran it."""
        out = []
        for deck, i in zip(decks, self.assign(decks)):
            named = self.named[i] if self.named else ()
            if all(c in set(deck) for c in named):
                out.append(self.names[i])
            else:
                key = _deck_key(deck, self._wincons)
                out.append(_key_name(self.vocab, key) if key else self.names[i])
        return out

    def unseen_wincon(self, deck: Sequence[int]) -> str | None:
        """The deck's win condition if no training deck had it (its archetype is then
        only the closest one), else None."""
        if not any(self.keys):
            return None
        key = _deck_key(deck, self._wincons)
        if key and not any(k[:1] == key[:1] for k in self.keys):
            return _key_name(self.vocab, key[:1])
        return None

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
            "keys": [list(k) for k in self.keys],
            "named": [list(c) for c in self.named],
        })

    @classmethod
    def from_json(cls, text: str) -> "ArchetypeModel":
        data = json.loads(text)
        if data.get("format") not in (ARCHETYPE_FORMAT, *OLD_FORMATS):
            raise ValueError("not a DeckLens archetype model file")
        vocab = CardVocab.from_dict(data["vocab"])
        centroids = np.asarray(data["centroids"], dtype=np.float64)
        wins = np.asarray(data["wins"], dtype=np.float64)
        games = np.asarray(data["games"], dtype=np.float64)
        groups = np.asarray(data["groups"], dtype=int)
        m = len(data["names"])
        keys = [tuple(int(c) for c in k) for k in data.get("keys", [])]
        named = [tuple(int(c) for c in k) for k in data.get("named", [])]
        if (centroids.ndim != 2 or centroids.shape[1] != len(vocab)
                or groups.shape != (centroids.shape[0],) or wins.shape != (m, m)
                or games.shape != (m, m) or not np.all(np.isfinite(centroids))
                or (groups < 0).any() or (groups >= m).any()
                or (wins < 0).any() or (games < 0).any()
                or (keys and len(keys) != centroids.shape[0])
                or (named and len(named) != m)):
            raise ValueError("archetype model file is malformed")
        return cls(vocab, centroids, groups, [str(n) for n in data["names"]], wins, games,
                   float(data["prior_strength"]),
                   np.asarray(data["deck_counts"], dtype=np.float64), keys, named)

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
