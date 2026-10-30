-- Latest Ranked rating per player, so name search can put players near your
-- rating first (Ranked matchmaking pairs players of similar rating).
-- Safe to run more than once. Run as the database OWNER (the app role can't ALTER):
--
--   Get-Content db\migrations\003_player_rating.sql | docker compose exec -T db psql -U decklens -d decklens -v ON_ERROR_STOP=1 -f -

ALTER TABLE players ADD COLUMN IF NOT EXISTS rating INTEGER;
