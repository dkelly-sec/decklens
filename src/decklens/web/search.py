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
MAX_RESULTS = 10
MAX_RATING = 20_000

# Order: exact name matches first; then players whose rating is closest to
# yours (Ranked matchmaking pairs similar ratings, so your opponent is almost
# always among them); then best global rank; then most recently seen.
_SEARCH = r"""
    SELECT p.tag, p.name, p.clan_name, r.rank, p.rating
    FROM players p
    LEFT JOIN LATERAL (
        SELECT s.rank FROM player_rank_snapshots s
        WHERE s.tag = p.tag AND s.rank IS NOT NULL
        ORDER BY s.observed_at DESC LIMIT 1
    ) r ON true
    WHERE p.search_key LIKE %(pattern)s ESCAPE '\'
    ORDER BY
        (p.search_key = %(key)s) DESC,
        CASE WHEN %(near)s::int IS NULL THEN 0
             ELSE abs(coalesce(p.rating, -100000) - %(near)s::int) END,
        r.rank ASC NULLS LAST,
        p.last_seen DESC
    LIMIT %(limit)s
"""


def _like_prefix(key: str) -> str:
    """Escape LIKE wildcards (search keys never contain them, but don't rely on it)."""
    return key.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def search_players(conn: Connection, query: str, *, near_rating: int | None = None
                   ) -> list[dict[str, Any]]:
    key = search_key(query)
    if len(key) < MIN_QUERY:
        raise ValueError(f"type at least {MIN_QUERY} letters or digits of the name")
    if near_rating is not None and not 0 <= near_rating <= MAX_RATING:
        raise ValueError("rating out of range")
    with conn.cursor() as cur:
        cur.execute(_SEARCH, {"pattern": _like_prefix(key), "key": key, "near": near_rating,
                              "limit": MAX_RESULTS})
        return [{"tag": tag, "name": name, "clan": clan, "rank": rank, "rating": rating}
                for tag, name, clan, rank, rating in cur.fetchall()]
