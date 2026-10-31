"""Manage DeckLens website logins. There is no sign-up page: accounts only
come from here, run by whoever controls the database.

    python -m decklens.users add david          # asks for the password twice
    python -m decklens.users passwd david       # new password, logs out everywhere
    python -m decklens.users logout-all david   # invalidate every session cookie
    python -m decklens.users disable david      # block logins (enable to undo)
    python -m decklens.users remove david
    python -m decklens.users list

Uses ADMIN_DATABASE_URL if set, otherwise DATABASE_URL. That must be the
database owner: the crawler's and website's roles can't change logins.
Passwords are read with getpass, so they never appear on screen, in shell
history or in process listings.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from typing import Any, Callable

from .config import ConfigError, load_settings
from .web.auth import check_new_password, hash_password, normalise_username

_BUMP = "UPDATE web_users SET session_version = session_version + 1 WHERE username = %s"


def ask_new_password(prompt: Callable[[str], str] = getpass.getpass) -> str:
    password = prompt("New password: ")
    check_new_password(password)
    if prompt("Same password again: ") != password:
        raise ValueError("the passwords didn't match")
    return password


def add_user(conn: Any, username: str, password: str) -> None:
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("SELECT 1 FROM web_users WHERE username = %s", (username,))
        if cur.fetchone():
            raise ValueError(f"{username} already exists (use passwd to change the password)")
        cur.execute("INSERT INTO web_users (username, password_hash) VALUES (%s, %s)",
                    (username, hash_password(password)))


def _change(conn: Any, sql: str, params: tuple, username: str) -> None:
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(sql, params)
        if cur.rowcount != 1:
            raise ValueError(f"no user called {username}")


def set_password(conn: Any, username: str, password: str) -> None:
    """New password, and every existing session is logged out."""
    _change(conn, "UPDATE web_users SET password_hash = %s, "
                  "session_version = session_version + 1 WHERE username = %s",
            (hash_password(password), username), username)


def logout_everywhere(conn: Any, username: str) -> None:
    _change(conn, _BUMP, (username,), username)


def set_disabled(conn: Any, username: str, disabled: bool) -> None:
    _change(conn, "UPDATE web_users SET disabled = %s, "
                  "session_version = session_version + 1 WHERE username = %s",
            (disabled, username), username)


def remove_user(conn: Any, username: str) -> None:
    _change(conn, "DELETE FROM web_users WHERE username = %s", (username,), username)


def list_users(conn: Any) -> list[tuple[str, bool, Any]]:
    with conn.cursor() as cur:
        cur.execute("SELECT username, disabled, created_at FROM web_users ORDER BY username")
        return cur.fetchall()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage DeckLens website logins.")
    parser.add_argument("command", choices=["add", "passwd", "logout-all", "disable",
                                            "enable", "remove", "list"])
    parser.add_argument("username", nargs="?")
    args = parser.parse_args(argv)

    username = None
    if args.command != "list":
        username = normalise_username(args.username)
        if username is None:
            parser.error("usernames are 3-32 characters: a-z, 0-9, dot, dash, underscore")

    try:
        settings = load_settings(require_database=True)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    url = os.environ.get("ADMIN_DATABASE_URL") or settings.database_url

    import psycopg

    try:
        with psycopg.connect(url) as conn:
            if args.command == "list":
                for name, disabled, created in list_users(conn):
                    print(f"{name}{'  (disabled)' if disabled else ''}  created {created:%Y-%m-%d}")
                return 0
            if args.command == "add":
                add_user(conn, username, ask_new_password())
                print(f"added {username}")
            elif args.command == "passwd":
                set_password(conn, username, ask_new_password())
                print(f"changed the password for {username} and logged them out everywhere")
            elif args.command == "logout-all":
                logout_everywhere(conn, username)
                print(f"logged {username} out everywhere")
            elif args.command in ("disable", "enable"):
                set_disabled(conn, username, args.command == "disable")
                print(f"{args.command}d {username}")
            elif args.command == "remove":
                remove_user(conn, username)
                print(f"removed {username}")
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except psycopg.errors.InsufficientPrivilege:
        print("error: this database login can't change logins. Use the database owner "
              "(set ADMIN_DATABASE_URL).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
