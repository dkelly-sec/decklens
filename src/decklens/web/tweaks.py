"""Deck tweak finder: one-card swaps that real players of your deck already make,
and how those versions actually did, overall and among the top 10,000 and top
1,000 players (by each player's global rank when they played, so it works the
same on day one of a season as at the end).

Candidates come only from stored Ranked decks that share 7 of your 8 cards: the
card they run instead of one of yours is the swap ("Rocket for Fireball", "Evo
Giant Snowball for The Log"). So every suggestion is a version people really
play with your win condition, never a model guess like Graveyard in X-Bow.

Win rates are pulled toward the family's (decks sharing 6+ of your 8 cards) by
PULL games' worth, so a 3-0 variant doesn't top the list, and the table shows
how many games each number rests on.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Sequence

import numpy as np

from ..battles import FORM_TOKEN, is_form_token
from ..features import CardVocab
from ..model import wilson_interval
from .tiers import BattleTable

# (key, label, best rank allowed; None = everyone)
BANDS = (("all", "All players", None), ("10000", "Top 10,000", 10_000), ("1000", "Top 1,000", 1_000))

PULL = 20          # games' worth of the family's win rate added to every number
MIN_GAMES = 10     # a swap needs this many games in a band to be ranked there
TOP_SWAPS = 8


def _split(deck: Sequence[int]) -> tuple[frozenset[int], dict[int, int]]:
    """(plain cards, card -> its Evo/Hero token)."""
    base = frozenset(c for c in deck if not is_form_token(c))
    forms = {t % FORM_TOKEN["evo"]: t for t in deck if is_form_token(t)}
    return base, forms


def find_tweaks(t: BattleTable, vocab: CardVocab, deck: Sequence[int],
                sort_band: str = "all") -> dict[str, Any]:
    mine, my_forms = _split(deck)
    bands = [{"key": k, "label": label, "top": top} for k, label, top in BANDS]
    # Every stored side with its result and rank, both seats together.
    sides = []
    for decks, cards, ranks, won_if in ((t.decks0, t.cards0, t.rank0, 0),
                                        (t.decks1, t.cards1, t.rank1, 1)):
        sides.append((decks, t.overlap(cards, list(mine)), ranks, t.winner == won_if))
    result: dict[str, Any] = {"bands": [], "swaps": []}
    swaps: dict[tuple[int, int], dict[str, np.ndarray]] = defaultdict(
        lambda: {"wins": np.zeros(len(bands)), "games": np.zeros(len(bands))})
    you, family = np.zeros((len(bands), 2)), np.zeros((len(bands), 2))
    band_masks = [[np.ones(len(ranks), dtype=bool) if b["top"] is None else ranks <= b["top"]
                   for b in bands] for _, _, ranks, _ in sides]
    exact = tuple(sorted(mine)) + tuple(sorted(my_forms.values()))
    for (decks, overlap, _, won), masks in zip(sides, band_masks):
        for row in np.flatnonzero(overlap >= len(mine) - 2):
            in_bands = [i for i, m in enumerate(masks) if m[row]]
            w = float(won[row])
            for i in in_bands:
                family[i] += (w, 1)
            if overlap[row] == len(mine) and decks[row] == exact:
                for i in in_bands:
                    you[i] += (w, 1)
            elif overlap[row] == len(mine) - 1:
                base, forms = _split(decks[row])
                if len(base) != len(mine):
                    continue
                (old,), (new,) = mine - base, base - mine
                key = (old, forms.get(new, new))  # the Evo or Hero version counts as its own swap
                for i in in_bands:
                    swaps[key]["wins"][i] += w
                    swaps[key]["games"][i] += 1

    def pulled(wins: float, games: float, prior: float) -> float:
        return (wins + PULL * prior) / (games + PULL)

    gains, keys = {}, list(swaps)
    for i, b in enumerate(bands):
        fam_w, fam_n = family[i]
        info = {"key": b["key"], "label": b["label"], "family_games": int(fam_n),
                "games": int(you[i][1])}
        result["bands"].append(info)
        if fam_n < MIN_GAMES:
            continue
        prior = fam_w / fam_n
        base_rate = pulled(*you[i], prior)
        info["base"] = round(base_rate, 3)
        gains[b["key"]] = np.array([pulled(swaps[k]["wins"][i], swaps[k]["games"][i], prior)
                                    - base_rate for k in keys])
    if sort_band not in gains:
        return result
    sort_i = next(i for i, b in enumerate(bands) if b["key"] == sort_band)
    enough = np.array([swaps[k]["games"][sort_i] >= MIN_GAMES for k in keys], dtype=bool)
    for j in [j for j in np.argsort(-gains[sort_band]) if enough[j]][:TOP_SWAPS]:
        old, new = keys[j]
        s = swaps[keys[j]]
        cells = {}
        for i, b in enumerate(bands):
            if b["key"] in gains:
                n, w = int(s["games"][i]), int(s["wins"][i])
                lo, hi = wilson_interval(w, n) if n else (0.0, 1.0)
                cells[b["key"]] = {"gain": round(float(gains[b["key"]][j]), 3), "games": n,
                                   "rate": round(w / n, 3) if n else None,
                                   "range": [round(lo, 3), round(hi, 3)]}
        result["swaps"].append({"out": vocab.name(old), "in": vocab.name(new),
                                "out_id": old, "in_id": new, "bands": cells})
    return result
