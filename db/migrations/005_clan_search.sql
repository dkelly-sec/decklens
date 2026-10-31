-- Search players by clan, for opponents whose names are emoji or in a script
-- you can't type. Safe to run more than once. Run as the database OWNER:
--
--   Get-Content db\migrations\005_clan_search.sql | docker compose exec -T db psql -U decklens -d decklens -v ON_ERROR_STOP=1 -f -
--
-- clan_key is the clan name normalised like search_key (see names.py).
-- NULL means "not worked out yet": the next crawl or index refresh fills it in.

ALTER TABLE players ADD COLUMN IF NOT EXISTS clan_key TEXT;
CREATE INDEX IF NOT EXISTS players_clan_key_idx ON players (clan_key text_pattern_ops);
