"""Nightly crawl: seed from the Ranked leaderboard, then follow opponents.

    python -m decklens.crawl                     # leaderboard top 1,000, crawl up to 1,500 players
    python -m decklens.crawl --max-players 200   # smaller run
    python -m decklens.crawl --probe             # print response shapes only, store nothing

Top players mostly face each other, so each battle log reveals more highly
ranked players. Opponents are visited best-ranked first, within a fixed budget
of API requests, with a pause between requests.
"""

from __future__ import annotations

import argparse
import heapq
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, Protocol

from .api_client import ApiError, ClashRoyaleClient, NotFoundError
from .battles import Battle, parse_battle_log
from .config import ConfigError, load_settings
from .rankings import RankEntry, parse_leaderboard
from .validation import InvalidTagError, normalize_tag

log = logging.getLogger("decklens.crawl")

UNRANKED_PRIORITY = 1_000_000  # opponents without a known rank go to the back
RANKED_TYPE = "pathOfLegend"
MAX_FAILURES = 10


class Store(Protocol):
    def save_battles(self, battles: list[Battle]) -> int: ...
    def mark_crawled(self, tag: str) -> None: ...


@dataclass
class CrawlStats:
    players_crawled: int = 0
    not_found: int = 0
    failures: int = 0
    battles_seen: int = 0
    battles_new: int = 0
    discovered: int = 0
    skipped_types: dict[str, int] = field(default_factory=dict)


class CrawlAborted(RuntimeError):
    """Raised when continuing would be pointless or harmful (bad key, repeated failures)."""


def crawl(
    client: ClashRoyaleClient,
    store: Store,
    seeds: Iterable[tuple[int | None, str]],
    *,
    max_players: int,
    pause: float = 0.2,
    sleep: Callable[[float], None] = time.sleep,
) -> CrawlStats:
    """Visit up to max_players battle logs, best-ranked first. One request per player."""
    stats = CrawlStats()
    heap: list[tuple[int, int, str]] = []
    queued: set[str] = set()
    counter = 0

    def push(rank: int | None, tag: str) -> None:
        nonlocal counter
        if tag in queued:
            return
        queued.add(tag)
        counter += 1
        heapq.heappush(heap, (rank if rank is not None else UNRANKED_PRIORITY, counter, tag))

    for rank, tag in seeds:
        try:
            push(rank, normalize_tag(tag))
        except InvalidTagError:
            continue

    while heap and stats.players_crawled < max_players:
        _, _, tag = heapq.heappop(heap)
        if stats.players_crawled:
            sleep(pause)  # be polite: spread requests out
        try:
            entries = client.get_battle_log(tag)
        except NotFoundError:
            stats.not_found += 1
            continue
        except ApiError as exc:
            if exc.status == 403:
                raise CrawlAborted(str(exc)) from None  # bad key or IP: stop immediately
            stats.failures += 1
            log.warning("failed to fetch %s: %s", tag, exc)
            if stats.failures >= MAX_FAILURES:
                raise CrawlAborted(f"stopping after {stats.failures} failed requests") from None
            continue

        stats.players_crawled += 1
        battles, skipped = parse_battle_log(entries)
        for kind, n in skipped.items():
            stats.skipped_types[kind] = stats.skipped_types.get(kind, 0) + n
        stats.battles_seen += len(battles)
        stats.battles_new += store.save_battles(battles)
        store.mark_crawled(tag)

        for battle in battles:
            if battle.battle_type != RANKED_TYPE:
                continue  # only ranked games lead to more top players
            for p in battle.players:
                if p.tag not in queued:
                    stats.discovered += 1
                    push(p.global_rank, p.tag)

        if stats.players_crawled % 50 == 0:
            log.info(
                "progress: %d players, %d new battles, %d queued",
                stats.players_crawled, stats.battles_new, len(heap),
            )
    return stats


# --- seeding --------------------------------------------------------------


def fetch_leaderboard(
    client: ClashRoyaleClient, *, location: str, season: str | None, limit: int
) -> tuple[list[RankEntry], str]:
    """Current leaderboard, falling back to the latest finished season if it's empty."""
    if season:
        items = client.get_ranked_season_leaderboard(season, max_items=limit)
        return parse_leaderboard(items), "season"

    entries = parse_leaderboard(client.get_ranked_leaderboard(location, max_items=limit))
    if entries:
        return entries, "leaderboard"

    log.warning("current leaderboard came back empty; trying the latest finished season")
    seasons = client.get_seasons()
    if not seasons:
        return [], "leaderboard"
    latest = seasons[-1]
    season_id = str(latest.get("code") or latest.get("id") or "")
    if not season_id:
        return [], "leaderboard"
    log.info("using season %s", season_id)
    items = client.get_ranked_season_leaderboard(season_id, max_items=limit)
    return parse_leaderboard(items), "season"


# --- probe ----------------------------------------------------------------


def _shape(value: object, depth: int = 0) -> object:
    """Describe a JSON value's structure without printing personal data."""
    if isinstance(value, dict):
        if depth >= 2:
            return "{...}"
        return {k: _shape(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [f"{len(value)} items"] + ([_shape(value[0], depth + 1)] if value else [])
    return type(value).__name__


def probe(client: ClashRoyaleClient, location: str) -> None:
    """Print the structure of each response we depend on, to check field names."""
    board = client.get_ranked_leaderboard(location, max_items=3)
    print("Ranked leaderboard:", json.dumps(_shape(board), indent=2))
    seasons = client.get_seasons()
    print("Seasons (last 3):", json.dumps(seasons[-3:], indent=2))
    tag = next((i.get("tag") for i in board if isinstance(i, dict)), None)
    if tag:
        profile = client.get_player(tag)
        print("Profile Ranked result:",
              json.dumps(profile.get("currentPathOfLegendSeasonResult"), indent=2))
        ranked = [b for b in client.get_battle_log(tag) if b.get("type") == RANKED_TYPE]
        if ranked:
            print("Ranked battle entry:", json.dumps(_shape(ranked[0]), indent=2))
            team = ranked[0].get("team") or [{}]
            print("globalRank present:", "globalRank" in team[0],
                  "| leagueNumber:", ranked[0].get("leagueNumber"))


# --- CLI ------------------------------------------------------------------


class _PgStore:
    def __init__(self, conn, now: Callable[[], datetime]):
        self._conn, self._now = conn, now

    def save_battles(self, battles: list[Battle]) -> int:
        from .storage import save_battles

        return save_battles(self._conn, battles)

    def mark_crawled(self, tag: str) -> None:
        from .storage import mark_crawled

        mark_crawled(self._conn, tag, self._now())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed from the Ranked leaderboard and crawl.")
    parser.add_argument("--seed-leaderboard", type=int, default=1000, metavar="N",
                        help="leaderboard players to seed from (0 to skip)")
    parser.add_argument("--location", default="global", help="'global' or a numeric location id")
    parser.add_argument("--season", help="seed from a finished season's leaderboard instead")
    parser.add_argument("--max-players", type=int, default=1500,
                        help="battle logs to fetch this run (one API request each)")
    parser.add_argument("--recrawl-hours", type=float, default=20,
                        help="also revisit known players not crawled for this long")
    parser.add_argument("--pause", type=float, default=0.2, help="seconds between requests")
    parser.add_argument("--retention-days", type=int, default=90,
                        help="forget players not seen for this many days (0 to disable)")
    parser.add_argument("--probe", action="store_true",
                        help="print response structures and exit (no database needed)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if not 0 <= args.max_players <= 20_000 or not 0 <= args.seed_leaderboard <= 10_000:
        parser.error("--max-players must be 0-20000 and --seed-leaderboard 0-10000")
    if args.pause < 0.05:
        parser.error("--pause must be at least 0.05 seconds")

    try:
        settings = load_settings(require_database=not args.probe)
    except ConfigError as exc:
        log.error("%s", exc)
        return 2

    client = ClashRoyaleClient(settings.api_token, timeout=settings.request_timeout)
    try:
        if args.probe:
            probe(client, args.location)
            return 0

        import psycopg

        from .storage import players_due_for_crawl, prune_stale_players, save_rank_entries

        def now() -> datetime:
            return datetime.now(timezone.utc)

        with psycopg.connect(settings.database_url) as conn:
            seeds: list[tuple[int | None, str]] = []
            if args.seed_leaderboard:
                entries, source = fetch_leaderboard(
                    client, location=args.location, season=args.season,
                    limit=args.seed_leaderboard,
                )
                save_rank_entries(conn, entries, now(), source)
                seeds += [(e.rank, e.tag) for e in entries]
                log.info("seeded %d players from the %s", len(entries), source)

            due = players_due_for_crawl(
                conn, now=now(), recrawl_after=timedelta(hours=args.recrawl_hours),
                limit=args.max_players,
            )
            seeds += due
            log.info("%d known players due for a re-crawl", len(due))

            stats = crawl(client, _PgStore(conn, now), seeds,
                          max_players=args.max_players, pause=args.pause)
            log.info(
                "done: %d players crawled, %d battles seen, %d new, %d new players discovered, "
                "%d not found, %d failures",
                stats.players_crawled, stats.battles_seen, stats.battles_new,
                stats.discovered, stats.not_found, stats.failures,
            )
            if stats.skipped_types:
                log.info("skipped battle types: %s", stats.skipped_types)

            if args.retention_days:
                removed = prune_stale_players(conn, now=now(), keep_days=args.retention_days)
                log.info("retention: removed %d players not seen for %d days",
                         removed, args.retention_days)
    except (ApiError, CrawlAborted) as exc:
        log.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
