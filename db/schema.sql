-- DeckLens schema. Runs automatically the first time the Docker database starts.
-- Existing databases are upgraded with the files in db/migrations/ instead.

-- Every player DeckLens has seen. Powers opponent lookup by name.
CREATE TABLE IF NOT EXISTS players (
    tag             TEXT PRIMARY KEY CHECK (tag ~ '^#[0289PYLQGRJCUV]{3,15}$'),
    name            TEXT NOT NULL,        -- original display name: escape when rendering
    search_key      TEXT NOT NULL,        -- normalised letters/digits only (see names.py)
    clan_tag        TEXT,
    clan_name       TEXT,
    last_seen       TIMESTAMPTZ NOT NULL,
    last_crawled_at TIMESTAMPTZ,          -- when we last fetched this player's battle log
    rating          INTEGER               -- latest Ranked rating from a leaderboard, if known
);
-- text_pattern_ops makes prefix searches (search_key LIKE 'dav%') use the index.
CREATE INDEX IF NOT EXISTS players_search_key_idx ON players (search_key text_pattern_ops);

-- Rank and rating over time, so each battle can be placed in a skill tier
-- (Ultimate Champion / top 1,000 / top 200) as it was when it was played.
CREATE TABLE IF NOT EXISTS player_rank_snapshots (
    tag          TEXT NOT NULL REFERENCES players (tag) ON DELETE CASCADE,
    observed_at  TIMESTAMPTZ NOT NULL,
    rating       INTEGER,
    rank         INTEGER,
    source       TEXT NOT NULL DEFAULT 'leaderboard'
                 CHECK (source IN ('leaderboard', 'season', 'profile', 'battle')),
    PRIMARY KEY (tag, observed_at)
);

CREATE TABLE IF NOT EXISTS battles (
    battle_id     TEXT PRIMARY KEY,       -- sha256 of time + both tags, so duplicates collapse
    battle_time   TIMESTAMPTZ NOT NULL,
    battle_type   TEXT NOT NULL,
    game_mode     TEXT,
    league_number SMALLINT,               -- Ranked league the battle was played in
    winner_side   SMALLINT CHECK (winner_side IN (0, 1)),  -- NULL = draw
    collected_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS battles_time_idx ON battles (battle_time);

CREATE TABLE IF NOT EXISTS battle_players (
    battle_id          TEXT NOT NULL REFERENCES battles (battle_id) ON DELETE CASCADE,
    side               SMALLINT NOT NULL CHECK (side IN (0, 1)),
    player_tag         TEXT NOT NULL,
    crowns             SMALLINT NOT NULL,
    card_ids           INTEGER[] NOT NULL CHECK (cardinality(card_ids) = 8),
    cards              JSONB NOT NULL,
    starting_trophies  INTEGER,
    global_rank        INTEGER,           -- leaderboard rank at battle time (top players only)
    PRIMARY KEY (battle_id, side)
);
CREATE INDEX IF NOT EXISTS battle_players_tag_idx ON battle_players (player_tag);

-- Website logins (see db/migrations/004_web_users.sql and python -m decklens.users).
CREATE TABLE IF NOT EXISTS web_users (
    username        TEXT PRIMARY KEY CHECK (username ~ '^[a-z0-9_.-]{3,32}$'),
    password_hash   TEXT NOT NULL CHECK (password_hash LIKE 'scrypt$%'),
    session_version INTEGER NOT NULL DEFAULT 1,  -- bump to log out everywhere
    disabled        BOOLEAN NOT NULL DEFAULT false,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
