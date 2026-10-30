-- Week 2: rank at battle time, battle league, crawl bookkeeping.
-- Safe to run more than once. Run as the database OWNER (the app role can't ALTER):
--
--   Get-Content db\migrations\002_ranks_and_crawl.sql | docker compose exec -T db psql -U decklens -d decklens -v ON_ERROR_STOP=1 -f -

ALTER TABLE players         ADD COLUMN IF NOT EXISTS last_crawled_at TIMESTAMPTZ;
ALTER TABLE battles         ADD COLUMN IF NOT EXISTS league_number SMALLINT;
ALTER TABLE battle_players  ADD COLUMN IF NOT EXISTS global_rank INTEGER;
ALTER TABLE player_rank_snapshots
    ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'leaderboard';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'player_rank_snapshots_source_check'
    ) THEN
        ALTER TABLE player_rank_snapshots ADD CONSTRAINT player_rank_snapshots_source_check
            CHECK (source IN ('leaderboard', 'season', 'profile', 'battle'));
    END IF;
END $$;
