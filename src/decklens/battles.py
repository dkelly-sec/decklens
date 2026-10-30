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


def parse_battle_time(raw: str) -> datetime:
    """Parse the API's timestamp format, e.g. '20261030T011500.000Z'."""
    return datetime.strptime(raw, "%Y%m%dT%H%M%S.%fZ").replace(tzinfo=timezone.utc)


def make_battle_id(battle_time: str, tag_a: str, tag_b: str) -> str:
    lo, hi = sorted((tag_a, tag_b))
    return hashlib.sha256(f"{battle_time}|{lo}|{hi}".encode()).hexdigest()[:32]


def _player(raw: dict[str, Any]) -> BattlePlayer:
    cards = tuple(c for c in raw.get("cards", []) if isinstance(c, dict))
    if len(cards) != 8:
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


def parse_battle(raw: dict[str, Any]) -> Battle | None:
    """Return a Battle, or None if this entry isn't a kept 1v1 battle."""
    if raw.get("type") not in KEPT_BATTLE_TYPES:
        return None
    team, opponent = raw.get("team") or [], raw.get("opponent") or []
    if len(team) != 1 or len(opponent) != 1:
        return None
    try:
        a, b = _player(team[0]), _player(opponent[0])
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
        game_mode=(raw.get("gameMode") or {}).get("name"),
        players=players,
        winner=winner,
        league_number=opt_int(raw.get("leagueNumber")),
    )


def parse_battle_log(entries: Iterable[dict[str, Any]]) -> tuple[list[Battle], Counter]:
    """Parse a whole battle log. Returns kept battles and a count of skipped types."""
    kept: list[Battle] = []
    skipped: Counter = Counter()
    for entry in entries:
        battle = parse_battle(entry) if isinstance(entry, dict) else None
        if battle is None:
            skipped[str(entry.get("type") if isinstance(entry, dict) else "invalid")] += 1
        else:
            kept.append(battle)
    return kept, skipped
