"""Load stored ranked battles as matchups for training."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from psycopg import Connection

from .features import Matchup, level_gap

# Side 0 is the player with the lower tag, which is arbitrary with respect to
# who won. Ranks are each player's leaderboard rank when the battle was played.
_LOAD = """
    SELECT b.battle_id, b.battle_time, b.winner_side,
           p0.card_ids, p0.cards, p0.global_rank,
           p1.card_ids, p1.cards, p1.global_rank
    FROM battles b
    JOIN battle_players p0 ON p0.battle_id = b.battle_id AND p0.side = 0
    JOIN battle_players p1 ON p1.battle_id = b.battle_id AND p1.side = 1
    WHERE b.battle_type = %s
      AND b.winner_side IS NOT NULL
      AND (%s::smallint IS NULL OR b.league_number >= %s::smallint)
      AND (%s::timestamptz IS NULL OR b.battle_time >= %s::timestamptz)
    ORDER BY b.battle_time, b.battle_id
"""


def _card_names(cards: Any, names: dict[int, str]) -> None:
    for card in cards or []:
        if isinstance(card, dict) and isinstance(card.get("id"), int):
            name = card.get("name")
            if isinstance(name, str):
                names.setdefault(card["id"], name[:40])


def load_matchups(
    conn: Connection,
    *,
    battle_type: str = "pathOfLegend",
    min_league: int | None = None,
    since: datetime | None = None,
) -> tuple[list[Matchup], dict[int, str]]:
    """Decided battles of one type (optionally only since a time), oldest first,
    plus a card id -> name map."""
    matchups: list[Matchup] = []
    names: dict[int, str] = {}
    with conn.cursor() as cur:
        cur.execute(_LOAD, (battle_type, min_league, min_league, since, since))
        for bid, when, winner, ids_a, cards_a, rank_a, ids_b, cards_b, rank_b in cur:
            _card_names(cards_a, names)
            _card_names(cards_b, names)
            matchups.append(
                Matchup(
                    battle_id=bid,
                    battle_time=when,
                    deck_a=tuple(ids_a),
                    deck_b=tuple(ids_b),
                    level_gap_a=level_gap(cards_a or []),
                    level_gap_b=level_gap(cards_b or []),
                    a_won=1 if winner == 0 else 0,
                    rank_a=rank_a,
                    rank_b=rank_b,
                )
            )
    return matchups, names
