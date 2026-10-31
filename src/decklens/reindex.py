"""Recompute every player's search keys after the name-matching rules change.

    python -m decklens.reindex

Safe to run any time; only rows whose keys actually change are updated.
Uses DATABASE_URL (the crawler's role can update players).
"""

from __future__ import annotations

import logging
import sys

from .config import ConfigError, load_settings

log = logging.getLogger("decklens.reindex")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        settings = load_settings(require_database=True)
    except ConfigError as exc:
        log.error("%s", exc)
        return 2
    import psycopg

    from .storage import reindex_names

    with psycopg.connect(settings.database_url) as conn:
        changed = reindex_names(conn)
    log.info("reindex: updated search keys for %d players", changed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
