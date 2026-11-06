"""The ranked meta from stored battles: how often each archetype is played and how
it does, day by day, at the very top of the ladder: only battles where both
players were in the global top 1,000 (or top 200) when they played.

Uses the same in-memory battle table as the matchup rows (tiers.py), so it costs
a few numpy operations per request.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import numpy as np

from ..model import wilson_interval
from .tiers import TIERS, WINDOW_DAYS, BattleTable

TOP_ARCHETYPES = 15


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
