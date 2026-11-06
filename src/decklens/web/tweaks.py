"""Deck tweak finder: which one-card swap would most raise your win rate against
the current meta, overall and within each rating (medals) band.

The meta is the most played opponent decks in stored Ranked battles (the same
in-memory table as the matchup rows), weighted by how often each was played.
Every candidate deck is scored against all of them by the trees model, between
equally skilled players with equal card levels, so a gain means "this deck does
better against what people play", not "you'll win more if you're better".

Candidates swap one card for a popular card of the same kind (a spell for a
spell, a troop or building for a troop or building). The new card comes in plain:
its Evolution or Hero form isn't guessed. Swapping out an Evolution or Hero
drops that form with it.
"""

from __future__ import annotations

from collections import Counter
from itertools import compress
from typing import Any, Sequence

import numpy as np

from ..battles import FORM_TOKEN, is_form_token
from ..model import TreeMatchupModel
from .meta import in_band, rating_bands
from .services import card_kind
from .tiers import BattleTable

OPPONENT_DECKS = 60     # most played decks per band: the bulk of what you'll face
CARDS_PER_KIND = 30     # most played spells, and troops/buildings, to try swapping in (~2 s)
TOP_SWAPS = 8
VS_ARCHETYPES = 3       # the meta's top archetypes, for "+18% vs X-Bow Rocket"
MIN_DECKS = 30          # fewer stored decks in a band than this: no number for that band


def _group(card_id: int) -> str:
    return "spell" if card_kind(card_id) == "spell" else "unit"


def _meta(t: BattleTable, band: dict[str, Any]) -> tuple[list[tuple[int, ...]], np.ndarray,
                                                          np.ndarray, int]:
    """(top decks, play counts, archetype of each, decks in the band)."""
    counts: Counter = Counter()
    arch: dict[tuple[int, ...], int] = {}
    for decks, archs, ratings in ((t.decks0, t.arch0, t.rating0), (t.decks1, t.arch1, t.rating1)):
        mask = in_band(ratings, band)
        for d, a in zip(compress(decks, mask), archs[mask]):
            counts[d] += 1
            arch[d] = int(a)
    top = counts.most_common(OPPONENT_DECKS)
    return ([d for d, _ in top], np.array([n for _, n in top], dtype=float),
            np.array([arch[d] for d, _ in top], dtype=int), sum(counts.values()))


def candidates(deck: Sequence[int], popular: Sequence[int]) -> list[tuple[int, int, tuple[int, ...]]]:
    """(card out, card in, new deck) for every one-card swap worth trying."""
    base = [c for c in deck if not is_form_token(c)]
    forms = [c for c in deck if is_form_token(c)]
    out = []
    for old in base:
        pool = [c for c in popular if c not in base and _group(c) == _group(old)][:CARDS_PER_KIND]
        kept_forms = [f for f in forms if f % FORM_TOKEN["evo"] != old]
        for new in pool:
            out.append((old, new, tuple(sorted([c for c in base if c != old] + [new])) +
                        tuple(kept_forms)))
    return out


def find_tweaks(trees: TreeMatchupModel, t: BattleTable, names: list[str],
                deck: Sequence[int], sort_band: str = "all") -> dict[str, Any]:
    bands = rating_bands(t)
    metas = {b["key"]: _meta(t, b) for b in bands}
    all_decks, all_weights, _, _ = metas["all"]
    popularity: Counter = Counter()
    for d, w in zip(all_decks, all_weights):
        popularity.update({c: w for c in d if not is_form_token(c) and c in trees.vocab.index})
    swaps = candidates(deck, [c for c, _ in popularity.most_common()])
    decks = [tuple(deck)] + [s[2] for s in swaps]

    result: dict[str, Any] = {"bands": [], "swaps": []}
    gains: dict[str, np.ndarray] = {}
    vs: list[tuple[int, np.ndarray]] = []
    for b in bands:
        top, weights, archs, n_decks = metas[b["key"]]
        info = {"key": b["key"], "label": b["label"], "decks": n_decks}
        result["bands"].append(info)
        if n_decks < MIN_DECKS or not top:
            continue
        p = trees.proba_matrix(decks, top)                  # (1 + swaps) x opponents
        rates = p @ weights / weights.sum()
        info["base"] = round(float(rates[0]), 3)
        gains[b["key"]] = rates[1:] - rates[0]
        if b["key"] == "all":
            share = Counter()
            for a, w in zip(archs, weights):
                share[int(a)] += w
            for a, _ in share.most_common(VS_ARCHETYPES):
                mask = archs == a
                r = p[:, mask] @ weights[mask] / weights[mask].sum()
                vs.append((a, r[1:] - r[0]))

    if sort_band not in gains or not swaps:
        return result
    order = np.argsort(-gains[sort_band])[:TOP_SWAPS]
    vocab = trees.vocab
    for i in order:
        old, new, _ = swaps[i]
        result["swaps"].append({
            "out": vocab.name(old), "in": vocab.name(new), "out_id": old, "in_id": new,
            "gain": {k: round(float(g[i]), 3) for k, g in gains.items()},
            "vs": [{"archetype": names[a], "gain": round(float(g[i]), 3)} for a, g in vs],
        })
    return result
