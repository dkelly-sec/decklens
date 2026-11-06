"""Settings loaded from environment variables.

Secrets live only in the environment (or a local, gitignored .env file).
They are excluded from repr() so they never end up in logs or tracebacks.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or still a placeholder."""


_PLACEHOLDERS = {"", "replace-me", "changeme", "your-token-here"}


@dataclass(frozen=True)
class Settings:
    api_token: str = field(repr=False)
    database_url: str | None = field(default=None, repr=False)
    request_timeout: float = 10.0


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if value.lower() in _PLACEHOLDERS:
        raise ConfigError(f"{name} is not set. Copy .env.example to .env and fill it in.")
    return value


def my_tags() -> list[str]:
    """Your own player tags (DECKLENS_MY_TAGS, comma-separated), whose battles are
    collected so the app can show your own record. Invalid entries are skipped."""
    from .validation import InvalidTagError, normalize_tag

    tags = []
    for raw in os.environ.get("DECKLENS_MY_TAGS", "").split(",")[:10]:
        try:
            tag = normalize_tag(raw)
        except InvalidTagError:
            continue
        if tag not in tags:
            tags.append(tag)
    return tags


def load_settings(*, require_database: bool = True) -> Settings:
    """Read settings from the environment, loading a local .env file if present."""
    try:
        from dotenv import find_dotenv, load_dotenv

        # Look for .env in the folder you run from (usecwd=True), not next to
        # this source file. Real environment variables win over .env.
        env_file = find_dotenv(usecwd=True)
        if env_file:
            load_dotenv(env_file, override=False)
    except ImportError:  # pragma: no cover - dotenv is a declared dependency
        pass

    token = _required("CR_API_TOKEN")
    db_url = _required("DATABASE_URL") if require_database else os.environ.get("DATABASE_URL")
    return Settings(api_token=token, database_url=db_url)
