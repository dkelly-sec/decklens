"""Demo mode: the web app with sample players, and no API key, database or login.

    python -m decklens.web --demo      # then look up #2PPGJQ8V

For looking at DeckLens without setting anything up. Nothing here is real: the
players are invented, their battles are generated when the app starts, and the
model is fitted to games drawn from the made-up win rates in _EDGE below. The
page says so (a "Sample data" mark in the top bar). The rest is the real app:
the same routes, validation, rate limits and page, fed by DemoClient instead of
the Clash Royale API.

Not for a public server: there is no login. The command refuses to combine it
with DECKLENS_HTTPS=1, and it only ever listens on this computer.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any

from ..api_client import NotFoundError
from ..archetypes import ArchetypeModel
from ..features import CardVocab, Matchup
from ..model import LinearMatchupModel
from .services import Predictor
from .tiers import TierStats

# (id, name, elixir, rarity), as the Clash Royale API names them.
_CARDS = (
    (26000000, "Knight", 3, "common"), (26000001, "Archers", 3, "common"),
    (26000005, "Minions", 3, "common"), (26000006, "Balloon", 5, "epic"),
    (26000009, "Golem", 8, "epic"), (26000010, "Skeletons", 1, "common"),
    (26000011, "Valkyrie", 4, "rare"), (26000014, "Musketeer", 4, "rare"),
    (26000015, "Baby Dragon", 4, "epic"), (26000021, "Hog Rider", 4, "rare"),
    (26000023, "Ice Wizard", 3, "legendary"), (26000024, "Royal Giant", 6, "common"),
    (26000026, "Princess", 3, "legendary"), (26000029, "Lava Hound", 7, "legendary"),
    (26000030, "Ice Spirit", 1, "common"), (26000035, "Lumberjack", 4, "legendary"),
    (26000038, "Ice Golem", 2, "rare"), (26000039, "Mega Minion", 3, "rare"),
    (26000041, "Goblin Gang", 3, "common"), (26000044, "Hunter", 4, "epic"),
    (26000048, "Night Witch", 4, "legendary"), (26000050, "Royal Ghost", 3, "legendary"),
    (26000061, "Fisherman", 3, "legendary"),
    (27000000, "Cannon", 3, "common"), (27000003, "Inferno Tower", 5, "rare"),
    (27000006, "Tesla", 4, "common"), (27000008, "X-Bow", 6, "epic"),
    (27000009, "Tombstone", 3, "rare"),
    (28000000, "Fireball", 4, "rare"), (28000001, "Arrows", 3, "common"),
    (28000003, "Rocket", 6, "rare"), (28000004, "Goblin Barrel", 3, "epic"),
    (28000007, "Lightning", 6, "epic"), (28000009, "Poison", 4, "epic"),
    (28000010, "Graveyard", 5, "legendary"), (28000011, "The Log", 2, "legendary"),
    (28000012, "Tornado", 3, "epic"), (28000015, "Barbarian Barrel", 2, "epic"),
)
_BY_NAME = {name: (cid, elixir, rarity) for cid, name, elixir, rarity in _CARDS}

# Eight cards in the game's slot order, then a card some versions swap in for the last
# troop or building (so "7 of 8 cards the same" has something to find).
_DECKS: dict[str, tuple[tuple[str, ...], str]] = {
    "xbow": (("X-Bow", "Tesla", "Archers", "Knight", "Skeletons", "Ice Spirit",
              "Fireball", "The Log"), "Ice Golem"),
    "hog": (("Hog Rider", "Musketeer", "Ice Golem", "Ice Spirit", "Skeletons", "Cannon",
             "Fireball", "The Log"), "Knight"),
    "golem": (("Golem", "Night Witch", "Baby Dragon", "Lumberjack", "Mega Minion",
               "Tornado", "Lightning", "Barbarian Barrel"), "Minions"),
    "bait": (("Goblin Barrel", "Princess", "Knight", "Goblin Gang", "Ice Spirit",
              "Inferno Tower", "Rocket", "The Log"), "Valkyrie"),
    "lava": (("Lava Hound", "Balloon", "Mega Minion", "Minions", "Baby Dragon",
              "Tombstone", "Arrows", "Fireball"), "Musketeer"),
    "rg": (("Royal Giant", "Fisherman", "Hunter", "Royal Ghost", "Skeletons",
            "Ice Spirit", "Fireball", "The Log"), "Archers"),
    "yard": (("Graveyard", "Baby Dragon", "Ice Wizard", "Valkyrie", "Knight",
              "Tombstone", "Tornado", "Barbarian Barrel"), "Musketeer"),
}
_KEYS = tuple(_DECKS)

# Made-up chance that the row's deck beats the column's, for the sample games only.
# Loosely how players talk about these matchups; it is not measured from anything.
_EDGE = {
    ("xbow", "hog"): 0.55, ("xbow", "golem"): 0.30, ("xbow", "bait"): 0.50,
    ("xbow", "lava"): 0.42, ("xbow", "rg"): 0.38, ("xbow", "yard"): 0.56,
    ("hog", "golem"): 0.56, ("hog", "bait"): 0.47, ("hog", "lava"): 0.52,
    ("hog", "rg"): 0.50, ("hog", "yard"): 0.48,
    ("golem", "bait"): 0.46, ("golem", "lava"): 0.44, ("golem", "rg"): 0.53,
    ("golem", "yard"): 0.58,
    ("bait", "lava"): 0.55, ("bait", "rg"): 0.49, ("bait", "yard"): 0.54,
    ("lava", "rg"): 0.57, ("lava", "yard"): 0.60,
    ("rg", "yard"): 0.52,
}

YOU = "#2PPGJQ8V"
# tag: (name, clan tag, clan, deck, global rank or None, medals)
_PLAYERS: dict[str, tuple[str, str, str, str, int | None, int]] = {
    YOU: ("Demo Player", "#2QQ0YL", "Sample Clan", "xbow", 4312, 2140),
    "#8YQ2PLG9": ("GolemGrinder", "#9PPL2C", "Night Shift", "golem", 2877, 2215),
    "#9QJ8CYL0": ("Rocket Ronny", "#9PPL2C", "Night Shift", "bait", 6120, 2098),
    "#L8C2UVR0": ("hog_2.6_only", "#8GGU0R", "Cycle Club", "hog", 951, 2390),
    "#Q0GJ2Y8P": ("LavaLena", "#8GGU0R", "Cycle Club", "lava", None, 1985),
    "#PY90RC2L": ("RG Rafael", "#2QQ0YL", "Sample Clan", "rg", 3540, 2172),
    "#C8UV2QJ0": ("ミナ", "#2QQ0YL", "Sample Clan", "yard", 7702, 2051),
}
_CLANS = {clan_tag: name for _, clan_tag, name, _, _, _ in _PLAYERS.values()}
_COUNTRY = {"id": 57000017, "name": "Australia", "isCountry": True}


def _chance(a: str, b: str) -> float:
    if a == b:
        return 0.5
    return _EDGE[a, b] if (a, b) in _EDGE else 1 - _EDGE[b, a]


def _ids(key: str, variant: bool = False) -> tuple[int, ...]:
    names, swap = _DECKS[key]
    if variant:  # the last card that isn't a spell makes way for the swap
        last = max(i for i, n in enumerate(names) if _BY_NAME[n][0] < 28_000_000)
        names = names[:last] + (swap,) + names[last + 1:]
    return tuple(_BY_NAME[n][0] for n in names)


def _raw_cards(key: str) -> list[dict[str, Any]]:
    """A deck as the API sends it: maxed but for the first card, and no Evolutions
    or Heroes (which slot they sit in matters, and the sample decks don't model it)."""
    return [{"id": _BY_NAME[name][0], "name": name, "level": 16 if slot else 15, "maxLevel": 16,
             "elixirCost": _BY_NAME[name][1], "rarity": _BY_NAME[name][2]}
            for slot, name in enumerate(_DECKS[key][0])]


def _stamp(when: datetime) -> str:
    return when.strftime("%Y%m%dT%H%M%S.000Z")


class DemoClient:
    """Answers like ClashRoyaleClient, from the sample players above. The battle
    logs are generated once, relative to when the app started."""

    def __init__(self, now: datetime | None = None, seed: int = 7):
        self._now = now or datetime.now(timezone.utc)
        rng = random.Random(seed)  # nosec B311 - sample data, nothing secret rides on it
        self._logs: dict[str, list[dict[str, Any]]] = {tag: [] for tag in _PLAYERS}
        others = [t for t in _PLAYERS if t != YOU]
        # Your session: a game every few minutes, the Golem player most recently.
        order = ["#8YQ2PLG9"] + [others[i % len(others)] for i in range(1, 14)]
        for i, opp in enumerate(order):
            when = self._now - timedelta(minutes=4 + 7 * i)
            kind = "PvP" if i in (5, 9) else "pathOfLegend"
            self._play(rng, YOU, opp, when, kind)
        # And each opponent's own evening against the others.
        for n, tag in enumerate(others):
            for i in range(5):
                rival = others[(n + 1 + i) % len(others)]
                if rival != tag:
                    self._play(rng, tag, rival, self._now - timedelta(minutes=40 + 23 * i + n))
        for log in self._logs.values():
            log.sort(key=lambda b: b["battleTime"], reverse=True)  # newest first, like the API

    def _side(self, tag: str, crowns: int) -> dict[str, Any]:
        name, clan_tag, clan, deck, rank, _ = _PLAYERS[tag]
        side = {"tag": tag, "name": name, "crowns": crowns, "cards": _raw_cards(deck),
                "clan": {"tag": clan_tag, "name": clan}}
        if rank is not None and rank <= 500:
            side["globalRank"] = rank
        return side

    def _play(self, rng: random.Random, a: str, b: str, when: datetime,
              kind: str = "pathOfLegend") -> None:
        a_won = rng.random() < _chance(_PLAYERS[a][3], _PLAYERS[b][3])
        mode = {"name": "Ladder" if kind == "PvP" else "Ranked1v1_NewArena"}
        for me, them, won in ((a, b, a_won), (b, a, not a_won)):
            self._logs[me].append({
                "type": kind, "battleTime": _stamp(when), "gameMode": mode, "leagueNumber": 7,
                "team": [self._side(me, 2 if won else 0)],
                "opponent": [self._side(them, 0 if won else 2)],
            })

    def _known(self, tag: str) -> tuple[str, str, str, str, int | None, int]:
        if tag not in _PLAYERS:
            raise NotFoundError("not in the demo", 404)
        return _PLAYERS[tag]

    def get_player(self, tag: str) -> dict[str, Any]:
        name, clan_tag, clan, deck, rank, medals = self._known(tag)
        season = {"leagueNumber": 7, "trophies": medals}
        if rank is not None:
            season["rank"] = rank
        return {"tag": tag, "name": name, "trophies": 9000,
                "clan": {"tag": clan_tag, "name": clan},
                "currentPathOfLegendSeasonResult": season, "currentDeck": _raw_cards(deck)}

    def get_battle_log(self, tag: str) -> list[dict[str, Any]]:
        self._known(tag)
        return self._logs[tag]

    def get_clan(self, tag: str) -> dict[str, Any]:
        if tag not in _CLANS:
            raise NotFoundError("not in the demo", 404)
        return {"tag": tag, "name": _CLANS[tag], "location": dict(_COUNTRY)}

    def search_clans(self, name: str, *, limit: int = 10) -> list[dict[str, Any]]:
        want = name.strip().lower()
        return [{"tag": tag, "name": clan} for tag, clan in _CLANS.items()
                if want in clan.lower()][:limit]

    def get_clan_members(self, tag: str) -> list[dict[str, Any]]:
        if tag not in _CLANS:
            raise NotFoundError("not in the demo", 404)
        return [{"tag": ptag, "name": p[0], "trophies": 9000,
                 "lastSeen": _stamp(self._now - timedelta(minutes=3 * i))}
                for i, (ptag, p) in enumerate(_PLAYERS.items()) if p[1] == tag]

    def get_ranked_leaderboard(self, location_id: str = "global", *, max_items: int = 1000
                               ) -> list[dict[str, Any]]:
        ranked = sorted((p[4], tag, p[0]) for tag, p in _PLAYERS.items() if p[4] is not None)
        return [{"tag": tag, "name": name, "rank": 40 + 23 * i}
                for i, (_, tag, name) in enumerate(ranked)][:max_items]


def demo_predictor(games: int = 6000, seed: int = 7) -> Predictor:
    """Archetype and card models fitted to sample games, plus the battle table the
    record, meta and tweak pages read."""
    rng = random.Random(seed)  # nosec B311 - sample data
    now = datetime.now(timezone.utc)
    matchups, rows = [], []
    for k in range(games):
        a, b = rng.choice(_KEYS), rng.choice(_KEYS)
        deck_a = tuple(sorted(_ids(a, rng.random() < 0.3)))
        deck_b = tuple(sorted(_ids(b, rng.random() < 0.3)))
        won = int(rng.random() < _chance(a, b))
        rank_a, rank_b = rng.randint(1, 3000), rng.randint(1, 3000)
        when = now - timedelta(minutes=3 * k)  # the last 12 days or so
        matchups.append(Matchup(f"demo{k:05d}", when, deck_a, deck_b, 0.0, 0.0, won,
                                rank_a, rank_b))
        rows.append((0 if won else 1, deck_a, rank_a, deck_b, rank_b, when))
    matchups.sort(key=lambda m: m.battle_time)
    vocab = CardVocab.build(matchups, names={c[0]: c[1] for c in _CARDS},
                            elixir={c[0]: c[2] for c in _CARDS})
    archetypes = ArchetypeModel.fit(vocab, matchups, len(_KEYS), 20.0)
    predictor = Predictor(archetypes, LinearMatchupModel.fit(vocab, matchups))
    predictor.tiers = TierStats(archetypes, lambda: rows)
    return predictor
