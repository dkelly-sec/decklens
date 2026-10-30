"""Matchup models, evaluation and safe export.

Models
- LinearMatchupModel: logistic regression on the card-difference encoding.
  One weight per card ("strength") plus one for the level gap, no intercept,
  so identical decks score 50% and swapping decks flips the prediction.
  Exported as plain JSON, never pickle, so loading a model can't run code.
- TreeMatchupModel: gradient-boosted trees on the paired encoding, trained on
  both orientations of every battle and averaged both ways at prediction, so
  it is symmetric too. Can learn counters; kept in memory for evaluation only.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression, LogisticRegressionCV
from sklearn.model_selection import TimeSeriesSplit

from .features import (
    EXTRA_FEATURES, CardVocab, Matchup, encode_difference, encode_extras, encode_pair, flipped,
    labels,
)

MODEL_FORMAT = "decklens-linear-v2"
_EPS = 1e-6


# --- splitting ----------------------------------------------------------------


def time_split(
    matchups: Sequence[Matchup], test_fraction: float = 0.2
) -> tuple[list[Matchup], list[Matchup], datetime]:
    """Oldest battles train, newest test. Returns (train, test, cutoff time)."""
    if not 0 < test_fraction < 1:
        raise ValueError("test_fraction must be between 0 and 1")
    ordered = sorted(matchups, key=lambda m: (m.battle_time, m.battle_id))
    cut = int(len(ordered) * (1 - test_fraction))
    if cut == 0 or cut == len(ordered):
        raise ValueError("not enough battles to split")
    cutoff = ordered[cut].battle_time
    train = [m for m in ordered if m.battle_time < cutoff]
    test = [m for m in ordered if m.battle_time >= cutoff]
    return train, test, cutoff


# --- metrics ----------------------------------------------------------------------


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% confidence range for a proportion; behaves well for small n."""
    if n == 0:
        return (0.0, 1.0)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


@dataclass(frozen=True)
class Evaluation:
    name: str
    n: int
    accuracy: float
    accuracy_ci: tuple[float, float]
    log_loss: float
    brier: float
    calibration: list[tuple[float, float, int]]  # (mean predicted, actual win rate, count)


def evaluate(name: str, probs: np.ndarray, y: np.ndarray, bins: int = 10) -> Evaluation:
    probs = np.clip(np.asarray(probs, dtype=np.float64), _EPS, 1 - _EPS)
    y = np.asarray(y)
    # An exact 50% is a guess either way, so it counts as half right.
    decided = probs != 0.5
    right = float(((probs > 0.5).astype(int) == y)[decided].sum()) + 0.5 * float((~decided).sum())
    correct = int(round(right))
    ll = float(-np.mean(y * np.log(probs) + (1 - y) * np.log(1 - probs)))
    brier = float(np.mean((probs - y) ** 2))
    calibration = []
    edges = np.linspace(0, 1, bins + 1)
    for lo, hi in zip(edges[:-1], edges[1:]):
        mask = (probs >= lo) & ((probs < hi) if hi < 1 else (probs <= hi))
        count = int(mask.sum())
        if count:
            calibration.append((float(probs[mask].mean()), float(y[mask].mean()), count))
    return Evaluation(name, len(y), right / len(y), wilson_interval(correct, len(y)),
                      ll, brier, calibration)


# --- models -----------------------------------------------------------------------


class LinearMatchupModel:
    def __init__(self, vocab: CardVocab, card_weights: np.ndarray,
                 extra_weights: dict[str, float], trained_at: str = "",
                 meta: dict[str, Any] | None = None):
        if len(card_weights) != len(vocab):
            raise ValueError("one weight per card is required")
        if set(extra_weights) != set(EXTRA_FEATURES):
            raise ValueError(f"extra weights must be exactly {EXTRA_FEATURES}")
        self.vocab = vocab
        self.card_weights = np.asarray(card_weights, dtype=np.float64)
        self.extra_weights = {k: float(extra_weights[k]) for k in EXTRA_FEATURES}
        self.trained_at = trained_at
        self.meta = dict(meta or {})

    @property
    def level_weight(self) -> float:
        return self.extra_weights["level"]

    @property
    def skill_weight(self) -> float:
        return self.extra_weights["skill"]

    @classmethod
    def fit(cls, vocab: CardVocab, train: Sequence[Matchup]) -> "LinearMatchupModel":
        X = encode_difference(vocab, train)
        y = labels(train)
        # Regularisation strength picked by cross-validation that respects time:
        # each fold validates on battles later than the ones it trained on.
        clf = LogisticRegressionCV(
            Cs=np.logspace(-3, 1, 9), l1_ratios=(0.0,),  # pure L2 penalty
            cv=TimeSeriesSplit(n_splits=4),
            scoring="neg_log_loss", fit_intercept=False, max_iter=2000,
            use_legacy_attributes=False,  # opt in to the scikit-learn 1.10 behaviour now
        )
        clf.fit(X, y)
        w = clf.coef_[0]
        n = len(vocab)
        extras = dict(zip(EXTRA_FEATURES, (float(v) for v in w[n:])))
        return cls(vocab, w[:n], extras, meta={"C": float(np.atleast_1d(clf.C_)[0])})

    def _extra_vector(self) -> np.ndarray:
        return np.array([self.extra_weights[k] for k in EXTRA_FEATURES])

    def logits(self, matchups: Sequence[Matchup]) -> np.ndarray:
        X = encode_difference(self.vocab, matchups)
        n = len(self.vocab)
        return X[:, :n] @ self.card_weights + X[:, n:] @ self._extra_vector()

    def predict_proba(self, matchups: Sequence[Matchup]) -> np.ndarray:
        return 1.0 / (1.0 + np.exp(-self.logits(matchups)))

    def card_strengths(self) -> list[tuple[str, float]]:
        """(card name, weight), strongest first."""
        pairs = [(self.vocab.name(cid), float(w))
                 for cid, w in zip(self.vocab.card_ids, self.card_weights)]
        return sorted(pairs, key=lambda p: p[1], reverse=True)

    def explain(self, deck_a: Sequence[int], deck_b: Sequence[int], top: int = 5
                ) -> list[tuple[str, float]]:
        """Biggest card contributions to A's chances: positive helps A, negative helps B."""
        parts: dict[str, float] = {}
        for sign, deck, side in ((1, deck_a, "A"), (-1, deck_b, "B")):
            for cid in set(deck):
                col = self.vocab.index.get(cid)
                if col is not None:
                    parts[f"{self.vocab.name(cid)} ({side})"] = sign * float(self.card_weights[col])
        return sorted(parts.items(), key=lambda kv: abs(kv[1]), reverse=True)[:top]

    # Plain-JSON export: weights and card names only, safe to load.
    def to_json(self) -> str:
        return json.dumps({
            "format": MODEL_FORMAT,
            "trained_at": self.trained_at,
            "vocab": self.vocab.to_dict(),
            "card_weights": [round(float(w), 6) for w in self.card_weights],
            "extra_weights": {k: round(v, 6) for k, v in self.extra_weights.items()},
            "meta": self.meta,
        }, indent=1)

    @classmethod
    def from_json(cls, text: str) -> "LinearMatchupModel":
        data = json.loads(text)
        if data.get("format") != MODEL_FORMAT:
            raise ValueError("not a DeckLens linear model file (or an older format: retrain)")
        weights = np.asarray(data["card_weights"], dtype=np.float64)
        extras = {k: float(v) for k, v in data["extra_weights"].items()}
        if not np.all(np.isfinite(weights)) or not all(map(math.isfinite, extras.values())):
            raise ValueError("model file contains invalid weights")
        return cls(CardVocab.from_dict(data["vocab"]), weights, extras,
                   str(data.get("trained_at", "")), data.get("meta"))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8")


class TreeMatchupModel:
    def __init__(self, vocab: CardVocab, clf: HistGradientBoostingClassifier):
        self.vocab, self.clf = vocab, clf

    @classmethod
    def fit(cls, vocab: CardVocab, train: Sequence[Matchup]) -> "TreeMatchupModel":
        both = list(train) + [flipped(m) for m in train]  # every battle from both sides
        # No built-in early stopping: its random validation split would put a
        # battle in training and its mirror image in validation, which leaks.
        clf = HistGradientBoostingClassifier(
            learning_rate=0.05, max_iter=200, max_leaf_nodes=15, min_samples_leaf=40,
            l2_regularization=1.0, early_stopping=False, random_state=0,
        )
        clf.fit(encode_pair(vocab, both), labels(both))
        return cls(vocab, clf)

    def predict_proba(self, matchups: Sequence[Matchup]) -> np.ndarray:
        forward = self.clf.predict_proba(encode_pair(self.vocab, matchups))[:, 1]
        backward = self.clf.predict_proba(
            encode_pair(self.vocab, [flipped(m) for m in matchups]))[:, 1]
        return (forward + (1.0 - backward)) / 2.0


class ExtrasOnlyBaseline:
    """Logistic regression on a subset of the extra features, e.g. skill only."""

    def __init__(self, columns: Sequence[str]):
        self.cols = [EXTRA_FEATURES.index(c) for c in columns]
        self.clf = LogisticRegression(fit_intercept=False)

    def fit(self, train: Sequence[Matchup]) -> "ExtrasOnlyBaseline":
        self.clf.fit(encode_extras(train)[:, self.cols], labels(train))
        return self

    def predict_proba(self, matchups: Sequence[Matchup]) -> np.ndarray:
        return self.clf.predict_proba(encode_extras(matchups)[:, self.cols])[:, 1]
