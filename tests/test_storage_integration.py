"""Storage tests against a real Postgres.

Skipped unless DECKLENS_TEST_DATABASE_URL points at a throwaway database
(CI provides one). Never point it at a database you care about: tables are
truncated before every test.
"""

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from decklens.battles import parse_battle_log
from decklens.rankings import RankEntry

from test_battles import battle  # noqa: E402  (shared fixture data)

psycopg = pytest.importorskip("psycopg")
URL = os.environ.get("DECKLENS_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="DECKLENS_TEST_DATABASE_URL not set")

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def conn():
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute((ROOT / "db" / "schema.sql").read_text())
        for migration in sorted((ROOT / "db" / "migrations").glob("*.sql")):
            c.execute(migration.read_text())  # migrations must be safe to re-run
        c.execute("TRUNCATE battle_players, battles, player_rank_snapshots, players CASCADE")
        yield c


def test_battle_stores_rank_at_battle_time_and_league(conn):
    from decklens.storage import save_battles

    raw = battle()
    raw["leagueNumber"] = 10
    raw["team"][0]["globalRank"] = 150
    battles, _ = parse_battle_log([raw])
    assert save_battles(conn, battles) == 1
    assert save_battles(conn, battles) == 0

    league = conn.execute("SELECT league_number FROM battles").fetchone()[0]
    ranks = dict(conn.execute("SELECT player_tag, global_rank FROM battle_players").fetchall())
    snaps = conn.execute("SELECT tag, rank, source FROM player_rank_snapshots").fetchall()
    assert league == 10
    assert ranks == {"#2PP": 150, "#9QJ": None}
    assert snaps == [("#2PP", 150, "battle")]


def test_crawl_queue_prefers_best_rank_and_skips_recent(conn):
    from decklens.storage import mark_crawled, players_due_for_crawl, save_rank_entries

    entries = [
        RankEntry("#9QJ", "Second", 2600, 2, None, None),
        RankEntry("#2PP", "First", 2700, 1, None, None),
        RankEntry("#8YY", "Unranked", None, None, None, None),
    ]
    assert save_rank_entries(conn, entries, NOW, "leaderboard") == 3
    due = players_due_for_crawl(conn, now=NOW, recrawl_after=timedelta(hours=20), limit=10)
    assert [tag for tag, _ in due] == ["#2PP", "#9QJ", "#8YY"]

    mark_crawled(conn, "#2PP", NOW - timedelta(hours=1))   # crawled recently: not due
    mark_crawled(conn, "#9QJ", NOW - timedelta(hours=30))  # crawled long ago: due again
    due = players_due_for_crawl(conn, now=NOW, recrawl_after=timedelta(hours=20), limit=10)
    assert [tag for tag, _ in due] == ["#9QJ", "#8YY"]


def test_retention_removes_old_players_and_their_snapshots(conn):
    from decklens.storage import prune_stale_players, save_rank_entries

    save_rank_entries(conn, [RankEntry("#2PP", "Old", 1, 1, None, None)],
                      NOW - timedelta(days=100), "leaderboard")
    save_rank_entries(conn, [RankEntry("#9QJ", "Recent", 1, 2, None, None)],
                      NOW - timedelta(days=5), "leaderboard")
    assert prune_stale_players(conn, now=NOW, keep_days=90) == 1
    assert conn.execute("SELECT tag FROM players").fetchall() == [("#9QJ",)]
    assert conn.execute("SELECT count(*) FROM player_rank_snapshots").fetchone()[0] == 1


def test_hostile_names_are_stored_as_plain_text(conn):
    from decklens.storage import save_rank_entries

    evil = "𝓡𝓸𝓫'); DROP TABLE players;--🔥"
    save_rank_entries(conn, [RankEntry("#2PP", evil, None, None, None, None)], NOW, "profile")
    assert conn.execute("SELECT name, search_key FROM players").fetchone() == (
        evil, "robdroptableplayers")


def test_training_loader_reads_decided_ranked_battles(conn):
    from decklens.dataset import load_matchups
    from decklens.storage import save_battles

    top = battle(team_tag="#2PP", opp_tag="#9QJ", team_crowns=3, opp_crowns=0)
    top["leagueNumber"] = 7
    top["team"][0]["globalRank"] = 40  # Alice was ranked 40th at the time
    for card in top["team"][0]["cards"]:
        card["maxLevel"] = 16  # Alice's cards are 2 levels below max
    low = battle(team_tag="#8YY", opp_tag="#9QJ", team_crowns=0, opp_crowns=1)
    low["battleTime"] = "20261030T020000.000Z"
    low["leagueNumber"] = 3
    draw = battle(team_tag="#LLL", opp_tag="#9QJ", team_crowns=1, opp_crowns=1)
    draw["battleTime"] = "20261030T030000.000Z"
    ladder = battle(team_tag="#QQQ", opp_tag="#9QJ", kind="PvP")
    ladder["battleTime"] = "20261030T040000.000Z"
    save_battles(conn, parse_battle_log([top, low, draw, ladder])[0])

    matchups, names = load_matchups(conn)
    assert len(matchups) == 2  # draw and non-ranked battle excluded
    first, second = matchups  # oldest first
    assert first.a_won == 1 and first.level_gap_a == 2.0 and first.level_gap_b == 0.0
    assert (first.rank_a, first.rank_b) == (40, None)
    assert second.a_won == 0  # side A (#8YY, lower tag) lost
    assert names[26000000] == "Card 0"

    only_top, _ = load_matchups(conn, min_league=7)
    assert [m.battle_id for m in only_top] == [first.battle_id]

    recent, _ = load_matchups(conn, since=datetime(2026, 10, 30, 1, 30, tzinfo=timezone.utc))
    assert [m.battle_id for m in recent] == [second.battle_id]  # the 01:15 battle is too old
