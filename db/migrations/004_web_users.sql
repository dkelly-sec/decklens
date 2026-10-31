-- Website logins. Safe to run more than once. Run as the database OWNER:
--
--   Get-Content db\migrations\004_web_users.sql | docker compose exec -T db psql -U decklens -d decklens -v ON_ERROR_STOP=1 -f -
--
-- Accounts are added with: python -m decklens.users add <name>
-- The website's role may only READ this table (to check logins); only the
-- owner can create accounts or change passwords.

CREATE TABLE IF NOT EXISTS web_users (
    username        TEXT PRIMARY KEY CHECK (username ~ '^[a-z0-9_.-]{3,32}$'),
    password_hash   TEXT NOT NULL CHECK (password_hash LIKE 'scrypt$%'),
    session_version INTEGER NOT NULL DEFAULT 1,  -- bump to log out everywhere
    disabled        BOOLEAN NOT NULL DEFAULT false,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decklens_web') THEN
        GRANT SELECT ON web_users TO decklens_web;
    END IF;
END $$;
