"""Scouting logic behind the API. Everything here takes already-fetched data or
injected collaborators, so it can be tested without network or database.

All text that came from the Clash Royale API (names, clan names, card names)
is untrusted: it is length-limited here and rendered with textContent in the
browser, never as HTML.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Sequence

from ..archetypes import ArchetypeModel
from ..battles import Battle, opt_int, parse_battle_log
from ..model import LinearMatchupModel, wilson_interval
from ..validation import InvalidTagError, normalize_tag

# Game modes DeckLens scouts. Checked against a real battle log (Oct 2026):
#   Ranked:            type "pathOfLegend", game mode "Ranked1v1_NewArena"
#   20-win challenge:  type "trail", game mode "Challenge_AllCards_EventDeck_NoSet"
# "trail" is also used for Trophy Road ("Ladder") and friendlies, so challenge
# games are picked out by their game mode name, not their type.
RANKED = "pathOfLegend"
CHALLENGE_GAME_MODE = "Challenge_AllCards_EventDeck_NoSet"
MODES = ("ranked", "challenge")
SCOUTED_TYPES = frozenset({RANKED, "trail"})


def mode_of(battle: Battle) -> str | None:
    """"ranked", "challenge" (the 20-win challenge) or None for anything else."""
    if battle.battle_type == RANKED:
        return "ranked"
    if battle.battle_type == "trail" and battle.game_mode == CHALLENGE_GAME_MODE:
        return "challenge"
    return None


def _scouted(battle_log: list[dict[str, Any]]) -> list[Battle]:
    battles, _ = parse_battle_log(battle_log, allow_hidden_card=True, types=SCOUTED_TYPES)
    return [b for b in battles if mode_of(b) is not None]
MIN_GAMES_FOR_CONFIDENCE = 30


def _text(value: Any, limit: int = 64) -> str:
    return str(value)[:limit] if isinstance(value, str) else ""


RARITIES = {"common", "rare", "epic", "legendary", "champion"}

# The API's evolutionLevel is a pair of flags for what the player OWNS, not what
# is active: 1 = Evolution unlocked, 2 = Hero unlocked, 3 = both. What actually
# plays depends on the slot (cards come in the game's slot order):
#   slot 1: Evolution slot     slot 2: Hero / Champion slot
#   slot 3: Wild slot (either) slots 4-8: plain cards, whatever is unlocked
EVO_BIT, HERO_BIT = 1, 2
EVO_SLOT, HERO_SLOT, WILD_SLOT = 0, 1, 2


def active_form(owned: int | None, slot: int) -> str | None:
    """"evo", "hero", "either" (Wild slot, both unlocked: the API doesn't say) or None."""
    if owned is None or not 0 <= owned <= 3:
        return None
    evo, hero = bool(owned & EVO_BIT), bool(owned & HERO_BIT)
    if slot == EVO_SLOT:
        return "evo" if evo else None
    if slot == HERO_SLOT:
        return "hero" if hero else None
    if slot == WILD_SLOT:
        return "either" if evo and hero else "evo" if evo else "hero" if hero else None
    return None


def _card(c: dict[str, Any], slot: int) -> dict[str, Any]:
    """One card's display details: only checked, typed values make it through."""
    level, max_level = opt_int(c.get("level")), opt_int(c.get("maxLevel"))
    below_max = max_level - level if level is not None and max_level is not None else None
    elixir = opt_int(c.get("elixirCost"))
    rarity = c.get("rarity")
    return {
        "id": c["id"],
        "name": _text(c.get("name"), 40) or str(c["id"]),
        "elixir": elixir if elixir is not None and 0 <= elixir <= 10 else None,
        "rarity": rarity.lower() if isinstance(rarity, str) and rarity.lower() in RARITIES else None,
        "below_max": below_max if below_max is not None and 0 <= below_max <= 20 else None,
        "form": active_form(opt_int(c.get("evolutionLevel")), slot),
    }


HIDDEN_CARD = {"id": None, "name": "Hidden Hero", "elixir": None, "rarity": None,
               "below_max": None, "form": "hero", "hidden": True}


def _cards(raw_cards: Any) -> list[dict[str, Any]]:
    """Card details in the game's slot order.

    A 7-card deck means the API left out a Hero sitting in the Hero slot, so a
    placeholder goes back into slot 2 and the other cards keep their real slots.
    """
    valid = [c for c in (raw_cards if isinstance(raw_cards, list) else [])
             if isinstance(c, dict) and opt_int(c.get("id")) is not None]
    if len(valid) == 7:
        out = [_card(valid[0], EVO_SLOT), dict(HIDDEN_CARD)]
        out += [_card(c, slot) for slot, c in enumerate(valid[1:], start=WILD_SLOT)]
        return out
    return [_card(c, slot) for slot, c in enumerate(valid)]


def card_ids(cards: Sequence[dict[str, Any]]) -> list[int]:
    """The known card ids in a deck (a hidden card has none)."""
    return [c["id"] for c in cards if c.get("id") is not None]


def deck_summary(cards: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Average elixir, 4-card cycle, evolutions and heroes, and how many cards are maxed."""
    costs = sorted(c["elixir"] for c in cards if c.get("elixir") is not None)
    levels = [c["below_max"] for c in cards if c.get("below_max") is not None]
    forms = Counter(c.get("form") for c in cards)
    return {
        "avg_elixir": round(sum(costs) / len(costs), 1) if costs else None,
        "cycle": sum(costs[:4]) if len(costs) >= 4 else None,
        "evolutions": forms["evo"],
        "heroes": forms["hero"],
        "either": forms["either"],
        "maxed": sum(1 for b in levels if b == 0) if levels else None,
        "levels_known": len(levels),
        "hidden": sum(1 for c in cards if c.get("hidden")),
    }


def _deck(cards: list[dict[str, Any]]) -> dict[str, Any]:
    return {"cards": cards, "summary": deck_summary(cards)}


def summarise_player(profile: dict[str, Any]) -> dict[str, Any]:
    clan = profile.get("clan") if isinstance(profile.get("clan"), dict) else {}
    ranked = profile.get("currentPathOfLegendSeasonResult")
    ranked = ranked if isinstance(ranked, dict) else {}
    deck = _cards(profile.get("currentDeck"))  # 7 from the API means a hidden Hero
    return {
        "tag": normalize_tag(profile.get("tag")),
        "name": _text(profile.get("name")),
        "clan": _text(clan.get("name")) or None,
        "league": opt_int(ranked.get("leagueNumber")),
        "rating": opt_int(ranked.get("trophies")),
        "rank": opt_int(ranked.get("rank")),
        "deck": _deck(deck if len(deck) == 8 else []),
    }


def _side(battle: Battle, tag: str) -> tuple[int, int] | None:
    """(this player's side, opponent's side) in a stored battle, or None."""
    for i, p in enumerate(battle.players):
        if p.tag == tag:
            return i, 1 - i
    return None


def last_opponent(tag: str, battle_log: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The opponent from this player's most recent Ranked or 20-win challenge
    battle (logs are newest first)."""
    tag = normalize_tag(tag)
    for b in _scouted(battle_log):
        sides = _side(b, tag)
        if sides is None:
            continue
        me, them = sides
        opp = b.players[them]
        result = "draw" if b.winner is None else ("won" if b.winner == me else "lost")
        return {
            "tag": opp.tag, "name": opp.name, "clan": opp.clan_name,
            "rank": opp.global_rank, "your_result": result, "mode": mode_of(b),
            "time": b.battle_time.isoformat(),
            "deck": _deck(_cards(list(opp.cards))),  # their own levels and evolutions
            "your_deck": _deck(_cards(list(b.players[me].cards))),
        }
    return None


# Our own labels for what a player has been playing (raw API strings are never
# echoed back). Game mode names are more specific than types, so they win.
GAME_MODE_NAMES = {
    "Ranked1v1_NewArena": "Ranked", CHALLENGE_GAME_MODE: "20-win challenge",
    "Ladder": "Trophy Road", "Friendly": "Friendlies", "Showdown_Friendly": "Friendlies",
}
BATTLE_TYPE_NAMES = {
    "pathOfLegend": "Ranked", "PvP": "Trophy Road", "trail": "Other challenges",
    "challenge": "Other challenges", "tournament": "Tournaments",
    "clanMate": "Friendlies", "friendly": "Friendlies", "boatBattle": "Boat battles",
    "riverRacePvP": "River Race", "riverRaceDuel": "River Race duels",
    "riverRaceDuelColosseum": "River Race duels", "quickplay": "Quick play",
}


def battle_mix(battle_log: list[dict[str, Any]]) -> dict[str, int]:
    """What a player has been playing lately, using our own labels only."""
    mix: Counter = Counter()
    for entry in battle_log:
        if not isinstance(entry, dict):
            mix["Other modes"] += 1
            continue
        mode = (entry.get("gameMode") or {}) if isinstance(entry.get("gameMode"), dict) else {}
        kind = entry.get("type")
        label = GAME_MODE_NAMES.get(mode.get("name")) if isinstance(mode.get("name"), str) else None
        if label is None:
            label = BATTLE_TYPE_NAMES.get(kind, "Other modes") if isinstance(kind, str) else "Other modes"
        mix[label] += 1
    return dict(mix.most_common())


def recent_decks(tag: str, battle_log: list[dict[str, Any]], top: int = 3
                 ) -> dict[str, list[dict[str, Any]]]:
    """A player's most-played decks in each scouted mode, with their record."""
    tag = normalize_tag(tag)
    played: dict[str, Counter] = {m: Counter() for m in MODES}
    won: dict[str, Counter] = {m: Counter() for m in MODES}
    latest: dict[tuple[str, tuple[int, ...]], dict[str, Any]] = {}
    for b in _scouted(battle_log):  # newest first
        sides = _side(b, tag)
        if sides is None:
            continue
        me, _ = sides
        mode, deck = mode_of(b), b.players[me].card_ids
        played[mode][deck] += 1
        won[mode][deck] += int(b.winner == me)
        if (mode, deck) not in latest:  # newest slot order, evolutions and heroes
            latest[(mode, deck)] = {"cards": list(b.players[me].cards),
                                    "last_played": b.battle_time.isoformat()}
    return {
        mode: [{"deck": _deck(_cards(latest[(mode, deck)]["cards"])), "games": n,
                "wins": won[mode][deck], "last_played": latest[(mode, deck)]["last_played"]}
               for deck, n in played[mode].most_common(top)]
        for mode in MODES
    }


@dataclass
class Predictor:
    archetypes: ArchetypeModel
    linear: LinearMatchupModel | None = None
    tiers: Any = None  # TierStats (records by tier and match level), with a database

    def known_cards(self) -> set[int]:
        return set(self.archetypes.vocab.card_ids)

    def predict(self, deck_a: Sequence[int], deck_b: Sequence[int]) -> dict[str, Any]:
        model = self.archetypes
        i, j = (int(x) for x in model.assign([tuple(deck_a), tuple(deck_b)]))
        chance, games = model.matchup(i, j)
        result: dict[str, Any] = {
            "archetype_a": model.names[i], "archetype_b": model.names[j],
            "win_chance": round(chance, 3), "games": games, "mirror": i == j,
        }
        if i != j and games:
            lo, hi = wilson_interval(int(model.wins[i, j]), games)
            result["record"] = {"wins": int(model.wins[i, j]), "games": games,
                                "range": [round(lo, 3), round(hi, 3)]}
        if i == j:
            result["note"] = "Mirror match: same archetype on both sides, so it's a coin flip on decks."
        elif games < MIN_GAMES_FOR_CONFIDENCE:
            result["note"] = (f"Only {games} recorded games between these archetypes, so this "
                              "estimate leans mostly on each archetype's overall record.")
        else:
            result["note"] = (f"Based on {games} ranked games between these archetypes. "
                              "Player skill still decides most games.")
        if min(len(deck_a), len(deck_b)) < 8:
            result["note"] += (" One deck has a Hero the Clash Royale API doesn't show, so "
                               "this is judged on the other seven cards.")
        if self.tiers is not None:
            breakdown = self.tiers.matchup(deck_a, deck_b, i, j)
            if breakdown is not None:
                result["breakdown"] = breakdown
        if self.linear is not None:
            result["key_cards"] = self._key_cards(deck_a, deck_b)
        return result

    def _key_cards(self, deck_a: Sequence[int], deck_b: Sequence[int], top: int = 4
                   ) -> list[dict[str, Any]]:
        """Cards with the biggest effect on your chances, and whose deck they're in."""
        vocab, weights = self.linear.vocab, self.linear.card_weights
        parts = []
        for side, sign, deck in (("yours", 1, deck_a), ("theirs", -1, deck_b)):
            for cid in set(deck):
                col = vocab.index.get(cid)
                if col is not None:
                    parts.append({"card": vocab.name(cid), "deck": side,
                                  "effect": round(sign * float(weights[col]), 3)})
        return sorted(parts, key=lambda p: abs(p["effect"]), reverse=True)[:top]


def validate_deck(deck: Sequence[Any], known: set[int]) -> tuple[int, ...]:
    """8 different, known card ids (or 7 when the API hid a Hero), or ValueError."""
    if not isinstance(deck, (list, tuple)) or len(deck) not in (7, 8):
        raise ValueError("a deck must have 8 cards (7 if one is a hidden Hero)")
    ids = []
    for c in deck:
        if opt_int(c) is None:
            raise ValueError("card ids must be whole numbers")
        ids.append(c)
    if len(set(ids)) != len(ids):
        raise ValueError("a deck can't contain the same card twice")
    unknown = [c for c in ids if c not in known]
    if len(unknown) > 2:
        raise ValueError("too many cards the model has never seen")
    return tuple(sorted(ids))


def safe_tag(raw: str) -> str:
    try:
        return normalize_tag(raw)
    except InvalidTagError:
        raise ValueError("that isn't a valid player tag") from None
