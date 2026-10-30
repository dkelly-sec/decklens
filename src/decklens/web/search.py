"""Opponent search by name over the player index.

Names are matched on their normalised search key (see names.py), so emoji,
fancy fonts and look-alike letters don't get in the way. Results are capped,
queries must be at least 2 letters after normalisation, and the database role
used here should only be able to read (see db/web_role.sql).
"""

from __future__ import annotations

from typing import Any

from psycopg import Connection

from ..names import search_key

MIN_QUERY = 2
MAX_RESULTS = 5

_SEARCH = r"""
    SELECT p.tag, p.name, p.clan_name, r.rank
    FROM players p
    LEFT JOIN LATERAL (
        SELECT s.rank FROM player_rank_snapshots s
        WHERE s.tag = p.tag AND s.rank IS NOT NULL
        ORDER BY s.observed_at DESC LIMIT 1
    ) r ON true
    WHERE p.search_key LIKE %s ESCAPE '\'
    ORDER BY
        CASE WHEN %s::int IS NULL THEN 0 ELSE abs(coalesce(r.rank, 100000) - %s::int) END,
        r.rank ASC NULLS LAST,
        p.last_seen DESC
    LIMIT %s
"""


def _like_prefix(key: str) -> str:
    """Escape LIKE wildcards (search keys never contain them, but don't rely on it)."""
    return key.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def search_players(conn: Connection, query: str, *, near_rank: int | None = None
                   ) -> list[dict[str, Any]]:
    key = search_key(query)
    if len(key) < MIN_QUERY:
        raise ValueError(f"type at least {MIN_QUERY} letters or digits of the name")
    with conn.cursor() as cur:
        cur.execute(_SEARCH, (_like_prefix(key), near_rank, near_rank, MAX_RESULTS))
        return [{"tag": tag, "name": name, "clan": clan, "rank": rank}
                for tag, name, clan, rank in cur.fetchall()]
