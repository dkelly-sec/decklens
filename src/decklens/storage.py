"""Write battles and players to Postgres.

Every query is parameterised (%s placeholders): values are sent separately
from the SQL text, so player names and tags can never inject SQL.
"""

from __future__ import annotations

from typing import Iterable

from psycopg import Connection
from psycopg.types.json import Jsonb

from .battles import Battle
from .names import search_key

_INSERT_BATTLE = """
    INSERT INTO battles (battle_id, battle_time, battle_type, game_mode, winner_side)
    VALUES (%s, %s, %s, %s, %s)
    ON CONFLICT (battle_id) DO NOTHING
    RETURNING battle_id
"""

_INSERT_SIDE = """
    INSERT INTO battle_players
        (battle_id, side, player_tag, crowns, card_ids, cards, starting_trophies)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
"""

_UPSERT_PLAYER = """
    INSERT INTO players (tag, name, search_key, clan_tag, clan_name, last_seen)
    VALUES (%s, %s, %s, %s, %s, %s)
    ON CONFLICT (tag) DO UPDATE SET
        name = EXCLUDED.name,
        search_key = EXCLUDED.search_key,
        clan_tag = EXCLUDED.clan_tag,
        clan_name = EXCLUDED.clan_name,
        last_seen = EXCLUDED.last_seen
    WHERE players.last_seen < EXCLUDED.last_seen
"""


def save_battles(conn: Connection, battles: Iterable[Battle]) -> int:
    """Store battles in one transaction. Returns how many were new."""
    new = 0
    with conn.transaction(), conn.cursor() as cur:
        for battle in battles:
            for p in battle.players:
                cur.execute(
                    _UPSERT_PLAYER,
                    (p.tag, p.name, search_key(p.name), p.clan_tag, p.clan_name,
                     battle.battle_time),
                )
            cur.execute(
                _INSERT_BATTLE,
                (battle.battle_id, battle.battle_time, battle.battle_type,
                 battle.game_mode, battle.winner),
            )
            if cur.fetchone() is None:
                continue  # already stored from the other player's log
            new += 1
            for side, p in enumerate(battle.players):
                cur.execute(
                    _INSERT_SIDE,
                    (battle.battle_id, side, p.tag, p.crowns, list(p.card_ids),
                     Jsonb(list(p.cards)), p.starting_trophies),
                )
    return new
