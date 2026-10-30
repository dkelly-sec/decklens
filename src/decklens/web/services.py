"""Scouting logic behind the API. Everything here takes already-fetched data or
injected collaborators, so it can be tested without network or database.

All text that came from the Clash Royale API (names, clan names, card names)
is untrusted: it is length-limited here and rendered with textContent in the
browser, never as HTML.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence

from ..archetypes import ArchetypeModel
from ..battles import Battle, opt_int, parse_battle_log
from ..features import Matchup
from ..model import LinearMatchupModel, wilson_interval
from ..validation import InvalidTagError, normalize_tag

RANKED = "pathOfLegend"
MIN_GAMES_FOR_CONFIDENCE = 30
_EPOCH = datetime(2000, 1, 1, tzinfo=timezone.utc)  # placeholder time for ad-hoc matchups


def _text(value: Any, limit: int = 64) -> str:
    return str(value)[:limit] if isinstance(value, str) else ""


def _cards(raw_cards: Any) -> list[dict[str, Any]]:
    """[{id, name}] for valid cards only, in a stable order."""
    out = []
    for c in raw_cards if isinstance(raw_cards, list) else []:
        if isinstance(c, dict) and opt_int(c.get("id")) is not None:
            out.append({"id": c["id"], "name": _text(c.get("name"), 40) or str(c["id"])})
    return sorted(out, key=lambda c: c["id"])


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
        "deck": deck if len(deck) == 8 else [],
    }


def _side(battle: Battle, tag: str) -> tuple[int, int] | None:
    """(this player's side, opponent's side) in a stored battle, or None."""
    for i, p in enumerate(battle.players):
        if p.tag == tag:
            return i, 1 - i
    return None


def _names_from_log(entries: Any) -> dict[int, str]:
    """Card id -> name, from every deck in a battle log."""
    names: dict[int, str] = {}
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        for side in list(entry.get("team") or []) + list(entry.get("opponent") or []):
            if isinstance(side, dict):
                for card in _cards(side.get("cards")):
                    names.setdefault(card["id"], card["name"])
    return names


def _deck_cards(ids: Sequence[int], names: dict[int, str]) -> list[dict[str, Any]]:
    return [{"id": cid, "name": names.get(cid, str(cid))} for cid in ids]


def last_ranked_opponent(tag: str, battle_log: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The opponent from this player's most recent ranked battle (logs are newest first)."""
    tag = normalize_tag(tag)
    names = _names_from_log(battle_log)
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
            "deck": _deck_cards(opp.card_ids, names),
            "your_deck": _deck_cards(b.players[me].card_ids, names),
        }
    return None


def recent_ranked_decks(tag: str, battle_log: list[dict[str, Any]], top: int = 3
                        ) -> list[dict[str, Any]]:
    """A player's most-played decks in their recent ranked battles, with their record."""
    tag = normalize_tag(tag)
    names = _names_from_log(battle_log)
    battles, _ = parse_battle_log(battle_log)
    played: Counter = Counter()
    won: Counter = Counter()
    for b in battles:
        sides = _side(b, tag)
        if b.battle_type != RANKED or sides is None:
            continue
        me, _ = sides
        deck = b.players[me].card_ids
        played[deck] += 1
        won[deck] += int(b.winner == me)
    return [{"deck": _deck_cards(deck, names), "games": n, "wins": won[deck]}
            for deck, n in played.most_common(top)]


@dataclass
class Predictor:
    archetypes: ArchetypeModel
    linear: LinearMatchupModel | None = None

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
        if self.linear is not None:
            m = Matchup("query", _EPOCH, tuple(deck_a), tuple(deck_b), 0.0, 0.0, 0)
            result["key_cards"] = [{"card": name, "effect": round(w, 3)}
                                   for name, w in self.linear.explain(m.deck_a, m.deck_b, top=4)]
        return result


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
