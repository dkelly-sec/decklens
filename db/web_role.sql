-- Read-only role for the web app (week 5). Run once as the database OWNER:
--
--   $pw = python -c "import secrets; print(secrets.token_urlsafe(24))"
--   Get-Content db\web_role.sql | docker compose exec -T db psql -U decklens -d decklens -v ON_ERROR_STOP=1 -v web_password=$pw -f -
--
-- Then set WEB_DATABASE_URL=postgresql://decklens_web:<that password>@127.0.0.1:5432/decklens
-- in .env. The website can only READ: the player index (for name search) and
-- stored battles (for win rates by tier) and logins (to check passwords). It can't
-- change or delete anything,
-- so a bug or attack on the web side can't damage the data.
--
-- Already created the role? Run db/web_grants.sql instead to add new permissions.

CREATE ROLE decklens_web LOGIN PASSWORD :'web_password';

GRANT CONNECT ON DATABASE decklens TO decklens_web;
GRANT USAGE ON SCHEMA public TO decklens_web;
GRANT SELECT ON players, player_rank_snapshots, battles, battle_players, web_users TO decklens_web;
