"""Reproduction for docs/audits/2026-11-07-upstream-data.md, finding F1.

crawl.main() opens one non-autocommit connection. players_due_for_crawl() runs a bare
SELECT on it, which leaves an implicit transaction open, so every later
`with conn.transaction()` is only a savepoint. When the crawl then stops (a 403 after a
public IP change, ten failed requests), the connection block rolls back everything the
run stored and the retention delete never runs.

These tests replay main()'s sequence (crawl.py, the `with psycopg.connect(...)` block)
with the real storage functions and crawl(), against a throwaway Postgres. They need
DECKLENS_TEST_DATABASE_URL, like tests/test_storage_integration.py.

Fixed: the attack tests now pass and stay as regression tests.
"""

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from decklens.api_client import ApiError
from decklens.crawl import CrawlAborted, _PgStore, crawl
from decklens.rankings import RankEntry

from test_battles import battle

psycopg = pytest.importorskip("psycopg")
URL = os.environ.get("DECKLENS_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="DECKLENS_TEST_DATABASE_URL not set")

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def fresh_schema():
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute((ROOT / "db" / "schema.sql").read_text())
        for migration in sorted((ROOT / "db" / "migrations").glob("*.sql")):
            c.execute(migration.read_text())
        c.execute("TRUNCATE battle_players, battles, player_rank_snapshots, players, web_users "
                  "CASCADE")


class TwoPlayers:
    """#2PP's battle log is fine; #9QJ's fetch fails with `second` (None: also fine)."""

    def __init__(self, second=None):
        self.second = second

    def get_battle_log(self, tag):
        if tag == "#9QJ" and self.second is not None:
            raise self.second
        return [battle(team_tag=tag, opp_tag="#8YY" if tag == "#9QJ" else "#9QJ")]


def nightly_run(client):
    """crawl.main()'s connection block, with the leaderboard already seeded."""
    from decklens.storage import players_due_for_crawl, save_rank_entries

    with psycopg.connect(URL) as conn:                       # as at crawl.py: non-autocommit
        save_rank_entries(conn, [RankEntry("#2PP", "Alice", 3000, 1, None, None),
                                 RankEntry("#9QJ", "Bob", 2990, 2, None, None)],
                          NOW, "leaderboard")
        seeds = players_due_for_crawl(conn, now=NOW, recrawl_after=timedelta(hours=20),
                                      limit=10)
        crawl(client, _PgStore(conn, lambda: NOW), seeds, max_players=10, pause=0,
              profiles=False, sleep=lambda s: None)


def stored_battles():
    with psycopg.connect(URL, autocommit=True) as c:
        return c.execute("SELECT count(*) FROM battles").fetchone()[0]


def test_battles_stored_before_a_403_survive_the_abort():
    with pytest.raises(CrawlAborted):
        nightly_run(TwoPlayers(second=ApiError("forbidden: /players/%239QJ/battlelog", 403)))
    assert stored_battles() == 1, "the first player's battle was rolled back by the abort"


def test_a_clean_run_stores_its_battles():
    nightly_run(TwoPlayers())
    assert stored_battles() == 2
