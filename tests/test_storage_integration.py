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


def test_opponent_search_by_name(conn):
    from decklens.storage import save_rank_entries
    from decklens.web.search import search_players

    entries = [
        RankEntry("#2PP", "⚔️⚡D I P O⚡⚔️", 2900, 900, "Clan", "Clan"),
        RankEntry("#9QJ", "Dipolar", 3100, 120, None, None),
        RankEntry("#8YY", "Dípo Fan", None, None, None, None),
        RankEntry("#LLL", "Someone else", 3500, 5, None, None),
    ]
    save_rank_entries(conn, entries, NOW, "leaderboard")

    found = search_players(conn, "dipo")
    assert [r["tag"] for r in found] == ["#2PP", "#9QJ", "#8YY"]  # exact name first, then rank
    assert found[0]["name"] == "⚔️⚡D I P O⚡⚔️" and found[0]["rating"] == 2900  # display name kept

    near = search_players(conn, "DIP", near_rating=3050)
    assert [r["tag"] for r in near] == ["#9QJ", "#2PP", "#8YY"]   # closest rating, unknown last

    assert search_players(conn, "zz") == []
    with pytest.raises(ValueError):
        search_players(conn, "%")                                     # nothing searchable


def test_web_role_can_only_read(conn):
    """The web app's role can read the player index and nothing else."""
    def drop_role():
        if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = 'decklens_web_test'").fetchone():
            conn.execute("REVOKE ALL ON players, player_rank_snapshots FROM decklens_web_test")
            conn.execute("DROP ROLE decklens_web_test")

    grants = (ROOT / "db" / "web_grants.sql").read_text().replace("decklens_web", "decklens_web_test")
    drop_role()
    conn.execute("CREATE ROLE decklens_web_test")
    conn.execute(grants)
    conn.execute("SET ROLE decklens_web_test")
    try:
        for table in ("players", "player_rank_snapshots", "battles", "battle_players"):
            conn.execute(f"SELECT count(*) FROM {table}")  # reading is allowed
        for statement in ("DELETE FROM players", "UPDATE players SET name = 'x'",
                          "DELETE FROM battles", "TRUNCATE battle_players",
                          "INSERT INTO battles (battle_id, battle_time, battle_type) "
                          "VALUES ('x', now(), 'x')"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(statement)
    finally:
        conn.execute("RESET ROLE")
        conn.execute("REVOKE ALL ON battles, battle_players FROM decklens_web_test")
        drop_role()



def test_tier_rows_include_recent_battles_with_known_or_unknown_ranks(conn):
    from decklens.storage import save_battles
    from decklens.web.tiers import load_rows

    recent = datetime.now(timezone.utc) - timedelta(days=1)
    stamp = recent.strftime("%Y%m%dT%H%M%S.000Z")
    both = battle(team_tag="#2PP", opp_tag="#9QJ")
    both["battleTime"] = stamp
    both["team"][0]["globalRank"], both["opponent"][0]["globalRank"] = 120, 450
    one = battle(team_tag="#8YY", opp_tag="#9QJ")
    one["battleTime"] = stamp
    one["team"][0]["globalRank"] = 30                 # opponent unranked: rank is None
    old = battle(team_tag="#LLL", opp_tag="#9QJ")     # dated 30 Oct 2026, both ranked
    old["team"][0]["globalRank"], old["opponent"][0]["globalRank"] = 10, 20
    save_battles(conn, parse_battle_log([both, one, old])[0])

    rows = load_rows(conn, days=14)
    assert any({r[2], r[4]} == {30, None} for r in rows)
    assert len(rows) == 2 + int(datetime.now(timezone.utc) - datetime(2026, 10, 30, 1, 15,
                                                                        tzinfo=timezone.utc)
                                < timedelta(days=14))
    winner, deck0, rank0, deck1, rank1 = next(r for r in rows if {r[2], r[4]} == {120, 450})
    assert len(deck0) == 8 and len(deck1) == 8 and winner in (0, 1)


def test_tier_ranks_beyond_500_come_from_the_nearest_leaderboard_snapshot(conn):
    from decklens.rankings import RankEntry
    from decklens.storage import save_battles, save_rank_entries
    from decklens.web.tiers import load_rows

    when = datetime.now(timezone.utc) - timedelta(days=2)
    stamp = when.strftime("%Y%m%dT%H%M%S.000Z")

    def entry(tag, rank):
        return RankEntry(tag=tag, name=tag, rating=2000, rank=rank, clan_tag=None, clan_name=None)

    # Nightly snapshots: one 3 hours after the battle (used), one 3 days later (too far).
    save_rank_entries(conn, [entry("#9QJ", 2400), entry("#8YY", 120)],
                      when + timedelta(hours=3), "leaderboard")
    save_rank_entries(conn, [entry("#9QJ", 50), entry("#2PP", 700)],
                      when + timedelta(days=3), "leaderboard")

    filled = battle(team_tag="#2PP", opp_tag="#9QJ")
    filled["battleTime"] = stamp
    filled["team"][0]["globalRank"] = 80              # from the battle itself
    clamped = battle(team_tag="#8YY", opp_tag="#LLL")
    clamped["battleTime"] = stamp
    clamped["opponent"][0]["globalRank"] = 300
    # #8YY has no battle rank, so it was outside the top 500 then: snapshot 120 -> 501
    unknown = battle(team_tag="#2PP", opp_tag="#UUU")  # #UUU never on the leaderboard
    unknown["battleTime"] = (when - timedelta(hours=1)).strftime("%Y%m%dT%H%M%S.000Z")
    unknown["team"][0]["globalRank"] = 90
    save_battles(conn, parse_battle_log([filled, clamped, unknown])[0])

    ranks = {frozenset((r[2], r[4])) for r in load_rows(conn, days=14)}
    assert ranks == {frozenset((80, 2400)), frozenset((300, 501)), frozenset((90, None))}


def test_country_players_are_searchable_but_get_no_rank(conn):
    from decklens.storage import save_players
    from decklens.web.search import search_players

    save_players(conn, [RankEntry(tag="#2PP", name="Ｃｏｕｎｔｒｙ ⚔ Star", rating=None, rank=None,
                                  clan_tag=None, clan_name="Clan")], NOW)
    assert search_players(conn, "country") == [
        {"tag": "#2PP", "name": "Ｃｏｕｎｔｒｙ ⚔ Star", "clan": "Clan", "rank": None, "rating": None}]
    assert conn.execute("SELECT count(*) FROM player_rank_snapshots").fetchone()[0] == 0


def test_same_name_players_are_ordered_by_closeness_to_my_rating(conn):
    """Eight players called AJ: the one near my rating (my likely opponent) comes first."""
    from decklens.storage import save_battles, save_players
    from decklens.web.search import search_players

    tags = ["#LPVGU8J2V", "#GGGCPPVLU", "#2J8VVR8QY", "#V28PY8LPC", "#UL2908CLJ", "#URGV9CGL"]
    ratings = [2900, 2079, 3300, None, 1800, 2600]
    save_players(conn, [RankEntry(t, "AJ", r, None, None, None) for t, r in zip(tags, ratings)],
                 NOW)
    save_players(conn, [RankEntry(f"#{c}2PP", f"Ajumador{c}", 2130, None, None, None)
                        for c in "GJLQRU"], NOW)  # longer names, even closer ratings

    found = search_players(conn, "aj", near_rating=2136)
    assert len(found) == 10
    assert [r["tag"] for r in found[:6]] == [
        "#GGGCPPVLU", "#UL2908CLJ", "#URGV9CGL", "#LPVGU8J2V", "#2J8VVR8QY", "#V28PY8LPC"]
    # rating gaps: 57, 336, 464, 764, 1164, then unknown rating last
    assert all(r["name"].startswith("Ajumador") for r in found[6:])

    # A later sighting in a battle (no rating there) keeps the leaderboard rating.
    raw = battle(team_tag="#GGGCPPVLU", opp_tag="#9QJ")
    raw["battleTime"] = (NOW + timedelta(days=1)).strftime("%Y%m%dT%H%M%S.000Z")
    save_battles(conn, parse_battle_log([raw])[0])
    assert conn.execute("SELECT rating FROM players WHERE tag = '#GGGCPPVLU'").fetchone() == (2079,)
