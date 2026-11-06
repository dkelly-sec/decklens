"""Parse raw battle-log entries into clean, deduplicated 1v1 battles.

The same battle appears in both players' logs, so each battle gets a
deterministic ID from its time and both tags, and the two sides are put in
a canonical order (lower tag first). Seen from either log, it is identical.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from .validation import InvalidTagError, normalize_tag

# Battle types kept for analysis. Confirmed Oct 2026: Ranked mode battles are
# type "pathOfLegend" (game mode "Ranked1v1_NewArena"); event challenges are
# "trail" and friendlies "clanMate", both skipped.
KEPT_BATTLE_TYPES = frozenset({"pathOfLegend", "PvP"})

# When a Hero sits in the Hero slot, the API leaves that card out of the deck
# entirely, so the deck arrives with 7 cards. Stored battles still need all 8
# (the models train on full decks); scouting accepts 7 and marks the gap.
DECK_SIZE = 8

# The 20-win challenge: type "trail" (shared with Trophy Road and friendlies),
# told apart by this game mode name (checked against a real battle log, Oct 2026).
CHALLENGE_GAME_MODE = "Challenge_AllCards_EventDeck_NoSet"
# Trophy Road: game mode "Ladder" (variants "Ladder_..."), seen as type "trail"
# and, in older logs, "PvP" (which may carry no game mode at all).
TROPHY_ROAD_MODE = "Ladder"


def is_trophy_road(battle_type: str, game_mode: str | None) -> bool:
    mode = game_mode or ""
    return (mode == TROPHY_ROAD_MODE or mode.startswith(TROPHY_ROAD_MODE + "_")
            or (battle_type == "PvP" and not mode))


@dataclass(frozen=True)
class BattlePlayer:
    tag: str
    name: str
    crowns: int
    card_ids: tuple[int, ...]  # sorted, so the same deck always looks the same
    cards: tuple[dict[str, Any], ...]  # raw card data (levels, evolutions)
    starting_trophies: int | None
    clan_tag: str | None
    clan_name: str | None
    global_rank: int | None = None  # Ranked leaderboard position at battle time, if ranked


@dataclass(frozen=True)
class Battle:
    battle_id: str
    battle_time: datetime
    battle_type: str
    game_mode: str | None
    players: tuple[BattlePlayer, BattlePlayer]  # canonical order: lower tag first
    winner: int | None  # index into players, None for a draw
    league_number: int | None = None  # Ranked league the battle was played in


def opt_int(value: Any) -> int | None:
    """Return value as an int if it is a plain integer, else None (never trust types)."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


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


# Evolutions and Heroes play very differently from the plain card (Evo Giant rages
# the troops behind him), so the models see an active form as an extra "card": a
# form token, the card id plus FORM_TOKEN[form], next to the plain card id.
FORM_TOKEN = {"evo": 1_000_000_000, "hero": 2_000_000_000}


def is_form_token(card_id: int) -> bool:
    return card_id >= FORM_TOKEN["evo"]


def form_token(card_id: int, form: str | None) -> int | None:
    return card_id + FORM_TOKEN[form] if form in FORM_TOKEN else None  # "either": unknown, none


def form_tokens(cards: Any) -> tuple[int, ...]:
    """Tokens for the active Evolutions and Heroes in raw API cards (in slot order)."""
    out = []
    for slot, c in enumerate(cards if isinstance(cards, (list, tuple)) else []):
        if isinstance(c, dict) and opt_int(c.get("id")) is not None:
            token = form_token(c["id"], active_form(opt_int(c.get("evolutionLevel")), slot))
            if token is not None:
                out.append(token)
    return tuple(sorted(out))


def parse_battle_time(raw: str) -> datetime:
    """Parse the API's timestamp format, e.g. '20261030T011500.000Z'."""
    return datetime.strptime(raw, "%Y%m%dT%H%M%S.%fZ").replace(tzinfo=timezone.utc)


def make_battle_id(battle_time: str, tag_a: str, tag_b: str) -> str:
    lo, hi = sorted((tag_a, tag_b))
    return hashlib.sha256(f"{battle_time}|{lo}|{hi}".encode()).hexdigest()[:32]


def _deck_cards(raw: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    cards = raw.get("cards")
    return tuple(c for c in cards if isinstance(c, dict)) if isinstance(cards, list) else ()


def _player(raw: dict[str, Any], allow_hidden_card: bool = False) -> BattlePlayer:
    cards = _deck_cards(raw)  # kept in the game's slot order
    if len(cards) != DECK_SIZE and not (allow_hidden_card and len(cards) == DECK_SIZE - 1):
        raise ValueError("expected an 8-card deck")
    clan = raw.get("clan") or {}
    return BattlePlayer(
        tag=normalize_tag(raw["tag"]),
        name=str(raw.get("name", ""))[:64],
        crowns=int(raw.get("crowns", 0)),
        card_ids=tuple(sorted(int(c["id"]) for c in cards)),
        cards=cards,
        starting_trophies=opt_int(raw.get("startingTrophies")),
        clan_tag=clan.get("tag"),
        clan_name=clan.get("name"),
        global_rank=opt_int(raw.get("globalRank")),
    )


def _game_mode_name(mode: Any) -> str | None:
    name = mode.get("name") if isinstance(mode, dict) else None
    return name[:80] if isinstance(name, str) else None


def parse_battle(raw: dict[str, Any], *, allow_hidden_card: bool = False,
                 types: frozenset[str] = KEPT_BATTLE_TYPES) -> Battle | None:
    """Return a Battle, or None if this entry isn't a kept 1v1 battle.

    allow_hidden_card accepts a 7-card deck (a Hero in the Hero slot, which the
    API omits). Only scouting uses it; stored battles always have 8 cards.
    types lets scouting read other battle types (e.g. challenges); the crawler
    keeps the default.
    """
    if raw.get("type") not in types:
        return None
    team, opponent = raw.get("team") or [], raw.get("opponent") or []
    if len(team) != 1 or len(opponent) != 1:
        return None
    try:
        a = _player(team[0], allow_hidden_card)
        b = _player(opponent[0], allow_hidden_card)
        when = parse_battle_time(raw["battleTime"])
    except (KeyError, ValueError, TypeError, InvalidTagError):
        return None  # malformed entries are skipped, never trusted

    players = (a, b) if a.tag < b.tag else (b, a)
    if players[0].crowns == players[1].crowns:
        winner = None
    else:
        winner = 0 if players[0].crowns > players[1].crowns else 1

    return Battle(
        battle_id=make_battle_id(raw["battleTime"], a.tag, b.tag),
        battle_time=when,
        battle_type=raw["type"],
        game_mode=_game_mode_name(raw.get("gameMode")),
        players=players,
        winner=winner,
        league_number=opt_int(raw.get("leagueNumber")),
    )


def _skip_reason(entry: Any) -> str:
    if not isinstance(entry, dict):
        return "invalid"
    kind = str(entry.get("type"))[:40]
    team, opponent = entry.get("team"), entry.get("opponent")
    one_v_one = (isinstance(team, list) and isinstance(opponent, list)
                 and len(team) == 1 and len(opponent) == 1)
    if kind in KEPT_BATTLE_TYPES and one_v_one:
        sides = team + opponent
        if any(isinstance(p, dict) and len(_deck_cards(p)) == DECK_SIZE - 1 for p in sides):
            return f"{kind} (7-card deck, hidden Hero)"
        return f"{kind} (unreadable)"
    return kind


def parse_battle_log(entries: Iterable[dict[str, Any]], *, allow_hidden_card: bool = False,
                     types: frozenset[str] = KEPT_BATTLE_TYPES) -> tuple[list[Battle], Counter]:
    """Parse a whole battle log. Returns kept battles and a count of skip reasons."""
    kept: list[Battle] = []
    skipped: Counter = Counter()
    for entry in entries:
        battle = (parse_battle(entry, allow_hidden_card=allow_hidden_card, types=types)
                  if isinstance(entry, dict) else None)
        if battle is None:
            skipped[_skip_reason(entry)] += 1
        else:
            kept.append(battle)
    return kept, skipped
