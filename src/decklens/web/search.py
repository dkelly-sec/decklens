"""Opponent search by name and/or clan over the player index.

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

# Filters: name prefix and/or clan prefix (either may be left out). Order:
# exact name, then exact clan matches first; then players whose rating is
# closest to yours (Ranked matchmaking pairs similar ratings, so your opponent
# is almost always among them); then best global rank; then most recently seen.
_SEARCH = r"""
    SELECT p.tag, p.name, p.clan_name, r.rank, p.rating
    FROM players p
    LEFT JOIN LATERAL (
        SELECT s.rank FROM player_rank_snapshots s
        WHERE s.tag = p.tag AND s.rank IS NOT NULL
        ORDER BY s.observed_at DESC LIMIT 1
    ) r ON true
    WHERE (%(pattern)s::text IS NULL OR p.search_key LIKE %(pattern)s ESCAPE '\')
      AND (%(clan_pattern)s::text IS NULL OR p.clan_key LIKE %(clan_pattern)s ESCAPE '\')
    ORDER BY
        (p.search_key = %(key)s) DESC,
        (p.clan_key = %(clan_key)s) DESC,
        CASE WHEN %(near)s::int IS NULL THEN 0
             ELSE abs(coalesce(p.rating, -100000) - %(near)s::int) END,
        r.rank ASC NULLS LAST,
        p.last_seen DESC
    LIMIT %(limit)s
"""


def _like_prefix(key: str) -> str:
    """Escape LIKE wildcards (search keys never contain them, but don't rely on it)."""
    return key.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def search_players(conn: Connection, query: str | None = None, *, clan: str | None = None,
                   near_rating: int | None = None) -> list[dict[str, Any]]:
    """Players by name, by clan, or both. A name with nothing typeable in it
    (all emoji, say) is ignored when a clan is given, so the clan alone works."""
    key = search_key(query) if query else ""
    clan_key = search_key(clan) if clan else ""
    use_name, use_clan = len(key) >= MIN_QUERY, len(clan_key) >= MIN_QUERY
    if not (use_name or use_clan):
        raise ValueError(f"type at least {MIN_QUERY} letters or digits of their name "
                         "or of their clan")
    if near_rating is not None and not 0 <= near_rating <= MAX_RATING:
        raise ValueError("rating out of range")
    params = {
        "pattern": _like_prefix(key) if use_name else None, "key": key or None,
        "clan_pattern": _like_prefix(clan_key) if use_clan else None,
        "clan_key": clan_key or None, "near": near_rating, "limit": MAX_RESULTS,
    }
    with conn.cursor() as cur:
        cur.execute(_SEARCH, params)
        return [{"tag": tag, "name": name, "clan": clan, "rank": rank, "rating": rating}
                for tag, name, clan, rank, rating in cur.fetchall()]
