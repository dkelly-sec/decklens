"""Opponent search by name and/or clan over the player index.

Names are matched on their normalised search key (see names.py), so emoji,
fancy fonts and look-alike letters don't get in the way, and fuzzily
(pg_trgm), so typos and half-remembered names still work. Results are capped,
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
FUZZY_THRESHOLD = 0.45

# Fuzzy search on name and/or clan. A player is a candidate if what you typed
# starts their name or clan, or closely resembles part of it (pg_trgm word
# similarity, so typos and partial names still match). Each side scores up to
# about 1 (exact 1.2, prefix 1.0, otherwise the similarity); with both typed,
# the best combined match wins. Ties (scores rounded to 0.1) go to the player
# whose rating is closest to yours, as Ranked pairs similar ratings.
_SEARCH = r"""
    SELECT p.tag, p.name, p.clan_name, r.rank, p.rating
    FROM players p
    LEFT JOIN LATERAL (
        SELECT s.rank FROM player_rank_snapshots s
        WHERE s.tag = p.tag AND s.rank IS NOT NULL
        ORDER BY s.observed_at DESC LIMIT 1
    ) r ON true
    CROSS JOIN LATERAL (
        SELECT
            CASE WHEN %(key)s::text IS NULL THEN 0
                 WHEN p.search_key = %(key)s THEN 1.2
                 WHEN p.search_key LIKE %(pattern)s ESCAPE '\' THEN 1.0
                 ELSE word_similarity(%(key)s, p.search_key) END
          + CASE WHEN %(clan_key)s::text IS NULL OR p.clan_key IS NULL THEN 0
                 WHEN p.clan_key = %(clan_key)s THEN 1.2
                 WHEN p.clan_key LIKE %(clan_pattern)s ESCAPE '\' THEN 1.0
                 ELSE word_similarity(%(clan_key)s, p.clan_key) END AS score
    ) m
    WHERE (%(key)s::text IS NOT NULL
           AND (p.search_key LIKE %(pattern)s ESCAPE '\' OR %(key)s <%% p.search_key))
       OR (%(clan_key)s::text IS NOT NULL
           AND (p.clan_key LIKE %(clan_pattern)s ESCAPE '\' OR %(clan_key)s <%% p.clan_key))
    ORDER BY
        round(m.score::numeric, 1) DESC,
        CASE WHEN %(near)s::int IS NULL THEN 0
             ELSE abs(coalesce(p.rating, -100000) - %(near)s::int) END,
        m.score DESC,
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
        "pattern": _like_prefix(key) if use_name else None, "key": key if use_name else None,
        "clan_pattern": _like_prefix(clan_key) if use_clan else None,
        "clan_key": clan_key if use_clan else None, "near": near_rating, "limit": MAX_RESULTS,
    }
    with conn.cursor() as cur:
        # How close a fuzzy match must be (pg_trgm's default 0.6 drops common
        # typos like "persainwarrior", 0.58). Set per search connection.
        cur.execute("SELECT set_config('pg_trgm.word_similarity_threshold', %s, false)",
                    (str(FUZZY_THRESHOLD),))
        cur.execute(_SEARCH, params)
        return [{"tag": tag, "name": name, "clan": clan, "rank": rank, "rating": rating}
                for tag, name, clan, rank, rating in cur.fetchall()]
