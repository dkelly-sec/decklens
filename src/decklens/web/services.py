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

RANKED = "pathOfLegend"
MIN_GAMES_FOR_CONFIDENCE = 30


def _text(value: Any, limit: int = 64) -> str:
    return str(value)[:limit] if isinstance(value, str) else ""


RARITIES = {"common", "rare", "epic", "legendary", "champion"}


def _card(c: dict[str, Any]) -> dict[str, Any]:
    """One card's display details: only checked, typed values make it through."""
    level, max_level = opt_int(c.get("level")), opt_int(c.get("maxLevel"))
    below_max = max_level - level if level is not None and max_level is not None else None
    elixir = opt_int(c.get("elixirCost"))
    rarity = c.get("rarity")
    evolution = opt_int(c.get("evolutionLevel"))
    return {
        "id": c["id"],
        "name": _text(c.get("name"), 40) or str(c["id"]),
        "elixir": elixir if elixir is not None and 0 <= elixir <= 10 else None,
        "rarity": rarity.lower() if isinstance(rarity, str) and rarity.lower() in RARITIES else None,
        "below_max": below_max if below_max is not None and 0 <= below_max <= 20 else None,
        "evolved": bool(evolution and evolution > 0),
    }


def _cards(raw_cards: Any) -> list[dict[str, Any]]:
    """Card details for valid cards only, in a stable order."""
    out = [_card(c) for c in (raw_cards if isinstance(raw_cards, list) else [])
           if isinstance(c, dict) and opt_int(c.get("id")) is not None]
    return sorted(out, key=lambda c: c["id"])


def deck_summary(cards: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Average elixir, 4-card cycle cost, evolutions and how many cards are maxed."""
    costs = sorted(c["elixir"] for c in cards if c.get("elixir") is not None)
    levels = [c["below_max"] for c in cards if c.get("below_max") is not None]
    return {
        "avg_elixir": round(sum(costs) / len(costs), 1) if costs else None,
        "cycle": sum(costs[:4]) if len(costs) >= 4 else None,
        "evolutions": sum(1 for c in cards if c.get("evolved")),
        "maxed": sum(1 for b in levels if b == 0) if levels else None,
    }


def _deck(cards: list[dict[str, Any]]) -> dict[str, Any]:
    return {"cards": cards, "summary": deck_summary(cards)}


def summarise_player(profile: dict[str, Any]) -> dict[str, Any]:
    clan = profile.get("clan") if isinstance(profile.get("clan"), dict) else {}
    ranked = profile.get("currentPathOfLegendSeasonResult")
    ranked = ranked if isinstance(ranked, dict) else {}
    deck = _cards(profile.get("currentDeck"))
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


def last_ranked_opponent(tag: str, battle_log: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The opponent from this player's most recent ranked battle (logs are newest first)."""
    tag = normalize_tag(tag)
    battles, _ = parse_battle_log(battle_log)
    for b in battles:
        sides = _side(b, tag)
        if b.battle_type != RANKED or sides is None:
            continue
        me, them = sides
        opp = b.players[them]
        result = "draw" if b.winner is None else ("won" if b.winner == me else "lost")
        return {
            "tag": opp.tag, "name": opp.name, "clan": opp.clan_name,
            "rank": opp.global_rank, "your_result": result,
            "time": b.battle_time.isoformat(),
            "deck": _deck(_cards(list(opp.cards))),  # their own levels and evolutions
            "your_deck": _deck(_cards(list(b.players[me].cards))),
        }
    return None


def recent_ranked_decks(tag: str, battle_log: list[dict[str, Any]], top: int = 3
                        ) -> list[dict[str, Any]]:
    """A player's most-played decks in their recent ranked battles, with their record."""
    tag = normalize_tag(tag)
    battles, _ = parse_battle_log(battle_log)
    played: Counter = Counter()
    won: Counter = Counter()
    latest: dict[tuple[int, ...], dict[str, Any]] = {}
    for b in battles:  # newest first
        sides = _side(b, tag)
        if b.battle_type != RANKED or sides is None:
            continue
        me, _ = sides
        deck = b.players[me].card_ids
        played[deck] += 1
        won[deck] += int(b.winner == me)
        if deck not in latest:
            latest[deck] = {"cards": list(b.players[me].cards),
                            "last_played": b.battle_time.isoformat()}
    return [{"deck": _deck(_cards(latest[deck]["cards"])), "games": n, "wins": won[deck],
             "last_played": latest[deck]["last_played"]}
            for deck, n in played.most_common(top)]


@dataclass
class Predictor:
    archetypes: ArchetypeModel
    linear: LinearMatchupModel | None = None
    tiers: Any = None  # TierStats, when the database is available

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
        if self.tiers is not None:
            result["tiers"] = self.tiers.matchup(i, j)
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
    """Exactly 8 different, known card ids, or ValueError."""
    if not isinstance(deck, (list, tuple)) or len(deck) != 8:
        raise ValueError("a deck must have exactly 8 cards")
    ids = []
    for c in deck:
        if opt_int(c) is None:
            raise ValueError("card ids must be whole numbers")
        ids.append(c)
    if len(set(ids)) != 8:
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
