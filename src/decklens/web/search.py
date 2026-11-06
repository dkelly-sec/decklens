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

# Stored games between two players (only Ranked and Trophy Road are stored).
_MEETINGS = """
    SELECT b.battle_time, b.battle_type = 'pathOfLegend',
           CASE WHEN b.winner_side IS NULL THEN NULL ELSE b.winner_side = me.side END,
           me.crowns, them.crowns, them.cards
    FROM battle_players me
    JOIN battle_players them ON them.battle_id = me.battle_id AND them.side <> me.side
    JOIN battles b ON b.battle_id = me.battle_id
    WHERE me.player_tag = %s AND them.player_tag = %s
    ORDER BY b.battle_time DESC
    LIMIT 10
"""


# One player's stored Ranked games: (time, did they win (None = draw), opponent's
# card ids, opponent's raw cards). Capped: a month of heavy play is well under this.
_MY_GAMES = """
    SELECT b.battle_time,
           CASE WHEN b.winner_side IS NULL THEN NULL ELSE b.winner_side = me.side END,
           them.card_ids, them.cards
    FROM battle_players me
    JOIN battle_players them ON them.battle_id = me.battle_id AND them.side <> me.side
    JOIN battles b ON b.battle_id = me.battle_id
    WHERE me.player_tag = %s AND b.battle_type = 'pathOfLegend'
      AND b.battle_time >= now() - make_interval(days => %s)
    ORDER BY b.battle_time DESC
    LIMIT 3000
"""


def my_games(conn: Connection, tag: str, days: int) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(_MY_GAMES, (tag, days))
        return cur.fetchall()


# Your rank over time: leaderboard and profile snapshots (current season's ranks).
_RANKS = """
    SELECT observed_at, rank, rating FROM player_rank_snapshots
    WHERE tag = %s AND source IN ('leaderboard', 'profile') AND rank IS NOT NULL
      AND observed_at >= now() - make_interval(days => %s)
    ORDER BY observed_at
    LIMIT 5000
"""


def rank_history(conn: Connection, tag: str, days: int) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(_RANKS, (tag, days))
        return cur.fetchall()


def stored_meetings(conn: Connection, me: str, them: str) -> list[tuple]:
    """(time, ranked?, did I win (None = draw), my crowns, their crowns, their cards)."""
    with conn.cursor() as cur:
        cur.execute(_MEETINGS, (me, them))
        return cur.fetchall()


MIN_QUERY = 2
MAX_RESULTS = 10
MAX_RATING = 20_000
FUZZY_THRESHOLD = 0.45
CLAN_MATCH = 0.7  # clan score at which a result counts as "in that clan"
# When the screen showed no clan under their name, clanless players get this
# much extra. Less than an exact name (1.2) is worth, because DeckLens may have
# stored someone's clan before they left it, or the clan line was missed.
NO_CLAN_BONUS = 0.5

# Fuzzy search on name and/or clan. A player is a candidate if what you typed
# starts their name or clan, or closely resembles part of it (pg_trgm word
# similarity, so typos and partial names still match). Each side scores up to
# about 1 (exact 1.2, prefix 1.0, otherwise the similarity); with both typed,
# the best combined match wins. Ties (scores rounded to 0.1) go to the player
# whose rating is closest to yours, as Ranked pairs similar ratings.
# no_clan (name only): the opponent had no clan, so players without one score
# NO_CLAN_BONUS more, and count as a match when their name fits well too.
_SEARCH = r"""
    SELECT p.tag, p.name, p.clan_name, r.rank, p.rating,
           CASE WHEN %(no_clan)s THEN p.clan_name IS NULL AND s.name_score >= %(clan_match)s
                ELSE m.clan_score >= %(clan_match)s END,
           s.name_score >= %(name_match)s
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
                 ELSE word_similarity(%(key)s, p.search_key) END AS name_score,
            CASE WHEN %(no_clan)s THEN CASE WHEN p.clan_name IS NULL THEN %(no_clan_bonus)s
                                            ELSE 0 END
                 WHEN %(clan_key)s::text IS NULL OR p.clan_key IS NULL THEN 0
                 WHEN p.clan_key = %(clan_key)s THEN 1.2
                 WHEN p.clan_key LIKE %(clan_pattern)s ESCAPE '\' THEN 1.0
                 ELSE word_similarity(%(clan_key)s, p.clan_key) END AS clan_score
    ) s
    CROSS JOIN LATERAL (SELECT s.name_score + s.clan_score AS score, s.clan_score) m
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
                   near_rating: int | None = None, no_clan: bool = False) -> list[dict[str, Any]]:
    """Players by name, by clan, or both. A name with nothing typeable in it
    (all emoji, say) is ignored when a clan is given, so the clan alone works.

    no_clan: the opponent is known to have no clan (the screen watcher saw
    none), so clanless players are preferred. Ignored if a clan is given."""
    key = search_key(query) if query else ""
    clan_key = search_key(clan) if clan else ""
    use_name, use_clan = len(key) >= MIN_QUERY, len(clan_key) >= MIN_QUERY
    if not (use_name or use_clan):
        raise ValueError(f"type at least {MIN_QUERY} letters or digits of their name "
                         "or of their clan")
    if near_rating is not None and not 0 <= near_rating <= MAX_RATING:
        raise ValueError("rating out of range")
    no_clan = bool(no_clan and use_name and not use_clan)
    params = {
        "pattern": _like_prefix(key) if use_name else None, "key": key if use_name else None,
        "clan_pattern": _like_prefix(clan_key) if use_clan else None,
        "clan_key": clan_key if use_clan else None, "near": near_rating, "limit": MAX_RESULTS,
        "clan_match": CLAN_MATCH, "no_clan": no_clan, "no_clan_bonus": NO_CLAN_BONUS,
        "name_match": FUZZY_THRESHOLD,
    }
    with conn.cursor() as cur:
        # How close a fuzzy match must be (pg_trgm's default 0.6 drops common
        # typos like "persainwarrior", 0.58). Set per search connection.
        cur.execute("SELECT set_config('pg_trgm.word_similarity_threshold', %s, false)",
                    (str(FUZZY_THRESHOLD),))
        cur.execute(_SEARCH, params)
        # clan_match: is this player in a clan like the one typed (or clanless,
        # with a close name, when no_clan)? If nobody is, the page asks the game
        # directly instead (live clan lookup), or says they aren't known yet.
        # name_match: does their name resemble what was typed (None if no name
        # was typed)? A clan match alone isn't "found them" when you gave a name:
        # five YEET members called Matt, rydawg... aren't DaHveed of YEET.
        return [{"tag": tag, "name": name, "clan": clan, "rank": rank, "rating": rating,
                 "clan_match": bool((use_clan or no_clan) and in_clan),
                 "name_match": bool(name_ok) if use_name else None}
                for tag, name, clan, rank, rating, in_clan, name_ok in cur.fetchall()]


# Clans DeckLens already knows with this name, by tag, most members first. The
# live clan check uses these directly: the game's own clan search wants the
# name spelled exactly ("RICH ESPORTS"), and screen reading often drops spaces
# or misreads a letter ("RICHESPORTS"), which the normalised key ignores.
_KNOWN_CLANS = r"""
    SELECT p.clan_tag, max(p.clan_name), count(*) AS members,
           max(CASE WHEN p.clan_key = %(key)s THEN 1.0 ELSE similarity(%(key)s, p.clan_key) END) AS score
    FROM players p
    WHERE p.clan_tag IS NOT NULL AND p.clan_key IS NOT NULL
      AND (p.clan_key = %(key)s OR %(key)s %% p.clan_key)
    GROUP BY p.clan_tag
    HAVING max(CASE WHEN p.clan_key = %(key)s THEN 1.0 ELSE similarity(%(key)s, p.clan_key) END)
           >= %(min_score)s
    ORDER BY score DESC, members DESC
    LIMIT %(limit)s
"""
KNOWN_CLAN_MIN_SCORE = 0.6
MAX_KNOWN_CLANS = 5


def known_clans(conn: Connection, clan: str) -> list[dict[str, Any]]:
    """[{"tag", "name"}] for clans in the index whose name is (close to) clan."""
    key = search_key(clan) if clan else ""
    if len(key) < MIN_QUERY:
        return []
    with conn.cursor() as cur:
        cur.execute(_KNOWN_CLANS, {"key": key, "min_score": KNOWN_CLAN_MIN_SCORE,
                                   "limit": MAX_KNOWN_CLANS})
        return [{"tag": tag, "name": name} for tag, name, _, _ in cur.fetchall()]
