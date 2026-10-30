"""Collect battle logs for a set of players and store them.

    python -m decklens.collector --tag "#YOURTAG" --dry-run   # fetch and summarise only
    python -m decklens.collector --tags-file tags.txt          # fetch and store

Week 1 starts from tags you supply. Seeding from the Ranked leaderboard and
crawling opponents comes next.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from pathlib import Path

from .api_client import ApiError, ClashRoyaleClient, NotFoundError
from .battles import Battle, parse_battle_log
from .config import ConfigError, load_settings
from .validation import InvalidTagError, normalize_tag

log = logging.getLogger("decklens.collector")

MAX_TAGS_PER_RUN = 1000


def _read_tags(args: argparse.Namespace) -> list[str]:
    raw = list(args.tag or [])
    if args.tags_file:
        lines = Path(args.tags_file).read_text(encoding="utf-8").splitlines()
        raw.extend(line for line in lines if line.strip() and not line.startswith("//"))

    tags: list[str] = []
    for item in raw:
        try:
            tag = normalize_tag(item)
        except InvalidTagError:
            log.warning("skipping invalid tag %r", item[:32])
            continue
        if tag not in tags:
            tags.append(tag)
    return tags[:MAX_TAGS_PER_RUN]


def collect(client: ClashRoyaleClient, tags: list[str]) -> tuple[list[Battle], Counter]:
    battles: dict[str, Battle] = {}
    skipped: Counter = Counter()
    for tag in tags:
        try:
            entries = client.get_battle_log(tag)
        except NotFoundError:
            log.warning("player %s not found", tag)
            continue
        kept, skip = parse_battle_log(entries)
        skipped.update(skip)
        for b in kept:
            battles.setdefault(b.battle_id, b)  # dedupe within this run
        log.info("%s: %d kept, %d skipped", tag, len(kept), sum(skip.values()))
    return list(battles.values()), skipped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect Clash Royale battle logs.")
    parser.add_argument("--tag", action="append", help="player tag (repeatable)")
    parser.add_argument("--tags-file", help="file with one tag per line")
    parser.add_argument("--dry-run", action="store_true", help="fetch and summarise, don't store")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    tags = _read_tags(args)
    if not tags:
        parser.error("give at least one valid --tag or --tags-file")

    try:
        settings = load_settings(require_database=not args.dry_run)
    except ConfigError as exc:
        log.error("%s", exc)
        return 2

    client = ClashRoyaleClient(settings.api_token, timeout=settings.request_timeout)
    try:
        battles, skipped = collect(client, tags)
    except ApiError as exc:
        log.error("%s", exc)
        return 1

    log.info("collected %d unique battles", len(battles))
    if skipped:
        log.info("skipped battle types: %s", dict(skipped))

    if args.dry_run:
        for b in battles[:5]:
            a, c = b.players
            result = "draw" if b.winner is None else f"{b.players[b.winner].name} won"
            when = b.battle_time.strftime("%Y-%m-%d %H:%M")
            log.info("%s  %s vs %s  (%s)", when, a.name, c.name, result)
        return 0

    import psycopg  # imported here so --dry-run works without a database

    from .storage import save_battles

    with psycopg.connect(settings.database_url) as conn:
        new = save_battles(conn, battles)
    log.info("stored %d new battles", new)
    return 0


if __name__ == "__main__":
    sys.exit(main())
