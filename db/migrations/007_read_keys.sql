-- Names read off the screen mix up I, l and 1, and O and 0. Search folds each group
-- to one letter (translate(key, 'i10', 'llo'), see web/search.py) so a misread
-- name still finds the player. These indexes keep that fast; search works without
-- them, only slower (it reads every player). Safe to run more than once. Run as
-- the database OWNER:
--
--   Get-Content db\migrations\007_read_keys.sql | docker compose exec -T db psql -U decklens -d decklens -v ON_ERROR_STOP=1 -f -
--
-- The expression must stay exactly as search.py writes it, or Postgres won't use
-- the index.

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX IF NOT EXISTS players_read_key_idx ON players ((translate(search_key, 'i10', 'llo')));
CREATE INDEX IF NOT EXISTS players_read_key_trgm_idx ON players
    USING gin ((translate(search_key, 'i10', 'llo')) gin_trgm_ops);
CREATE INDEX IF NOT EXISTS players_clan_read_key_idx ON players ((translate(clan_key, 'i10', 'llo')));
CREATE INDEX IF NOT EXISTS players_clan_read_key_trgm_idx ON players
    USING gin ((translate(clan_key, 'i10', 'llo')) gin_trgm_ops);
