"""Write battles, players and rank snapshots to Postgres.

Every query is parameterised (%s placeholders): values are sent separately
from the SQL text, so player names and tags can never inject SQL.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable

from psycopg import Connection
from psycopg.types.json import Jsonb

from .battles import Battle
from .names import search_key
from .rankings import RankEntry

_INSERT_BATTLE = """
    INSERT INTO battles (battle_id, battle_time, battle_type, game_mode, league_number, winner_side)
    VALUES (%s, %s, %s, %s, %s, %s)
    ON CONFLICT (battle_id) DO NOTHING
    RETURNING battle_id
"""

_INSERT_SIDE = """
    INSERT INTO battle_players
        (battle_id, side, player_tag, crowns, card_ids, cards, starting_trophies, global_rank)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
"""

_UPSERT_PLAYER = """
    INSERT INTO players (tag, name, search_key, clan_tag, clan_name, last_seen, rating)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (tag) DO UPDATE SET
        name = EXCLUDED.name,
        search_key = EXCLUDED.search_key,
        clan_tag = EXCLUDED.clan_tag,
        clan_name = EXCLUDED.clan_name,
        last_seen = EXCLUDED.last_seen,
        rating = COALESCE(EXCLUDED.rating, players.rating)
    WHERE players.last_seen < EXCLUDED.last_seen
"""

def _rating(value: int | None) -> int | None:
    """A plausible Ranked rating, or None (never store junk from the API)."""
    return value if value is not None and 0 <= value <= 20_000 else None


_INSERT_SNAPSHOT = """
    INSERT INTO player_rank_snapshots (tag, observed_at, rating, rank, source)
    VALUES (%s, %s, %s, %s, %s)
    ON CONFLICT (tag, observed_at) DO NOTHING
"""

# Players due a crawl: never crawled, or not since the cutoff. Best-ranked first,
# using each player's most recent known rank.
_DUE_FOR_CRAWL = """
    SELECT r.rank, p.tag
    FROM players p
    LEFT JOIN LATERAL (
        SELECT s.rank FROM player_rank_snapshots s
        WHERE s.tag = p.tag AND s.rank IS NOT NULL
        ORDER BY s.observed_at DESC LIMIT 1
    ) r ON true
    WHERE p.last_crawled_at IS NULL OR p.last_crawled_at < %s
    ORDER BY r.rank ASC NULLS LAST, p.last_crawled_at ASC NULLS FIRST
    LIMIT %s
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
                     battle.battle_time, None),
                )
                if p.global_rank is not None:  # rank as it was when this battle was played
                    cur.execute(
                        _INSERT_SNAPSHOT,
                        (p.tag, battle.battle_time, None, p.global_rank, "battle"),
                    )
            cur.execute(
                _INSERT_BATTLE,
                (battle.battle_id, battle.battle_time, battle.battle_type,
                 battle.game_mode, battle.league_number, battle.winner),
            )
            if cur.fetchone() is None:
                continue  # already stored from the other player's log
            new += 1
            for side, p in enumerate(battle.players):
                cur.execute(
                    _INSERT_SIDE,
                    (battle.battle_id, side, p.tag, p.crowns, list(p.card_ids),
                     Jsonb(list(p.cards)), p.starting_trophies, p.global_rank),
                )
    return new


def save_rank_entries(
    conn: Connection, entries: Iterable[RankEntry], observed_at: datetime, source: str
) -> int:
    """Store leaderboard or profile entries as players plus rank snapshots."""
    count = 0
    with conn.transaction(), conn.cursor() as cur:
        for e in entries:
            cur.execute(
                _UPSERT_PLAYER,
                (e.tag, e.name, search_key(e.name), e.clan_tag, e.clan_name, observed_at,
                 _rating(e.rating)),
            )
            cur.execute(_INSERT_SNAPSHOT, (e.tag, observed_at, e.rating, e.rank, source))
            count += 1
    return count


def save_players(conn: Connection, entries: Iterable[RankEntry], observed_at: datetime) -> int:
    """Store players for name search only, without a rank snapshot (used for
    country leaderboards, whose ranks are local, not global)."""
    count = 0
    with conn.transaction(), conn.cursor() as cur:
        for e in entries:
            cur.execute(
                _UPSERT_PLAYER,
                (e.tag, e.name, search_key(e.name), e.clan_tag, e.clan_name, observed_at,
                 _rating(e.rating)),
            )
            count += 1
    return count


def mark_crawled(conn: Connection, tag: str, when: datetime) -> None:
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("UPDATE players SET last_crawled_at = %s WHERE tag = %s", (when, tag))


def players_due_for_crawl(
    conn: Connection, *, now: datetime, recrawl_after: timedelta, limit: int
) -> list[tuple[str, int | None]]:
    """(last known rank, tag) for players not crawled recently, best-ranked first.

    The same (rank, tag) shape crawl() takes as seeds, so the two plug together.
    """
    with conn.cursor() as cur:
        cur.execute(_DUE_FOR_CRAWL, (now - recrawl_after, limit))
        return [(row[0], row[1]) for row in cur.fetchall()]


def prune_stale_players(conn: Connection, *, now: datetime, keep_days: int) -> int:
    """Retention policy: forget players not seen for keep_days. Returns rows deleted."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("DELETE FROM players WHERE last_seen < %s", (now - timedelta(days=keep_days),))
        return cur.rowcount
