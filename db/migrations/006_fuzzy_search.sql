-- Fuzzy name and clan search: typos and partial names still find the player.
-- Safe to run more than once. Run as the database OWNER (creating an extension
-- needs it; the Docker database's owner account can):
--
--   Get-Content db\migrations\006_fuzzy_search.sql | docker compose exec -T db psql -U decklens -d decklens -v ON_ERROR_STOP=1 -f -
--
-- pg_trgm ships with Postgres. It compares strings by their three-letter
-- chunks, so "persianwarior" still matches "persianwarrior".

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX IF NOT EXISTS players_search_key_trgm_idx ON players USING gin (search_key gin_trgm_ops);
CREATE INDEX IF NOT EXISTS players_clan_key_trgm_idx ON players USING gin (clan_key gin_trgm_ops);
