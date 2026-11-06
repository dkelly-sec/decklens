"""The ranked meta from stored battles: how often each archetype is played and how
it does, day by day, at the very top of the ladder: only battles where both
players were in the global top 1,000 (or top 200) when they played.

Uses the same in-memory battle table as the matchup rows (tiers.py), so it costs
a few numpy operations per request. Rating bands are cut from the data itself
(thirds of the players whose rating is known, rounded to 50), so they follow
wherever the ladder is this season.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import numpy as np

from ..model import wilson_interval
from .tiers import TIERS, WINDOW_DAYS, BattleTable

MIN_RATED_SIDES = 300   # fewer known ratings than this: no bands, just "all"
TOP_ARCHETYPES = 15


def rating_bands(t: BattleTable) -> list[dict[str, Any]]:
    """[{"key": "all"...}, then three bands by rating, lowest first]."""
    bands: list[dict[str, Any]] = [{"key": "all", "label": "All ratings", "min": None, "max": None}]
    known = np.concatenate([t.rating0, t.rating1])
    known = known[np.isfinite(known)]
    if len(known) < MIN_RATED_SIDES:
        return bands
    low, high = (int(round(q / 50) * 50) for q in np.quantile(known, [1 / 3, 2 / 3]))
    if high <= low:
        return bands
    bands += [
        {"key": "low", "label": f"Under {low:,}", "min": None, "max": low},
        {"key": "mid", "label": f"{low:,}–{high - 1:,}", "min": low, "max": high},
        {"key": "high", "label": f"{high:,} and up", "min": high, "max": None},
    ]
    return bands


def in_band(ratings: np.ndarray, band: dict[str, Any]) -> np.ndarray:
    """Sides whose rating falls in the band ("all" takes everyone, rated or not)."""
    if band["min"] is None and band["max"] is None:
        return np.ones(len(ratings), dtype=bool)
    ok = np.isfinite(ratings)
    if band["min"] is not None:
        ok &= ratings >= band["min"]
    if band["max"] is not None:
        ok &= ratings < band["max"]
    return ok


def meta(t: BattleTable, names: list[str], top: int = 1000,
         today: date | None = None) -> dict[str, Any]:
    if top not in TIERS:
        raise KeyError(top)
    today = today or date.today()
    days = WINDOW_DAYS
    side0 = side1 = t.worst <= top   # both players inside the tier at the time
    archs = np.concatenate([t.arch0[side0], t.arch1[side1]])
    other = np.concatenate([t.arch1[side0], t.arch0[side1]])
    won = np.concatenate([t.winner[side0] == 0, t.winner[side1] == 1])
    day = np.concatenate([t.days_ago[side0], t.days_ago[side1]])
    total = len(archs)
    out: dict[str, Any] = {"tiers": [{"top": n, "label": f"Top {n:,}"} for n in TIERS],
                           "top": top, "days": days, "decks": int(total),
                           "dates": [(today - timedelta(days=d)).isoformat()
                                     for d in range(days - 1, -1, -1)], "archetypes": []}
    if not total:
        return out
    per_day = np.array([(day == d).sum() for d in range(days - 1, -1, -1)])
    counts = np.bincount(archs, minlength=len(names))
    for a in np.argsort(-counts)[:TOP_ARCHETYPES]:
        if not counts[a]:
            break
        mine = archs == a
        fair = mine & (other != a)            # mirrors say nothing about strength
        games, wins = int(fair.sum()), int((fair & won).sum())
        lo, hi = wilson_interval(wins, games) if games else (0.0, 1.0)
        daily = []
        for i, d in enumerate(range(days - 1, -1, -1)):
            on_day = day == d
            g = int((fair & on_day).sum())
            daily.append({"share": round(float((mine & on_day).sum() / per_day[i]), 4)
                          if per_day[i] else None,
                          "win_rate": round((fair & on_day & won).sum() / g, 3) if g else None,
                          "games": g})
        out["archetypes"].append({
            "name": names[a], "share": round(float(counts[a] / total), 4), "games": games,
            "win_rate": round(wins / games, 3) if games else None,
            "range": [round(lo, 3), round(hi, 3)], "daily": daily,
        })
    return out
