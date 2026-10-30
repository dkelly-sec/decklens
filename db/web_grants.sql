-- Read-only permissions for the web app's role. Safe to run more than once.
-- Run as the database OWNER after creating the role with db/web_role.sql:
--
--   Get-Content db\web_grants.sql | docker compose exec -T db psql -U decklens -d decklens -v ON_ERROR_STOP=1 -f -

GRANT SELECT ON players, player_rank_snapshots, battles, battle_players TO decklens_web;
