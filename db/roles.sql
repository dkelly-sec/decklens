-- Least-privilege role for the app. Run once as the database owner:
--
--   psql "$DATABASE_URL" -v app_password="$(openssl rand -base64 24)" -f db/roles.sql
--
-- The password is passed in as a variable, so it is never stored in this file.
-- Then point the app's DATABASE_URL at decklens_app instead of the owner account.

CREATE ROLE decklens_app LOGIN PASSWORD :'app_password';

GRANT CONNECT ON DATABASE decklens TO decklens_app;
GRANT USAGE ON SCHEMA public TO decklens_app;

-- Read and write data, but no DROP, ALTER, TRUNCATE or DELETE on core tables.
GRANT SELECT, INSERT, UPDATE ON players, player_rank_snapshots, battles, battle_players
    TO decklens_app;
-- Retention clean-up (removing stale players) is the only delete the app needs.
GRANT DELETE ON players, player_rank_snapshots TO decklens_app;
