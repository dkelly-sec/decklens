import pytest

from decklens.api_client import ApiError, NotFoundError
from decklens.crawl import CrawlAborted, crawl, fetch_leaderboard


def ranked_battle(me, opp, opp_rank=None, kind="pathOfLegend", minute=0):
    def side(tag, rank, first):
        return {
            "tag": tag, "name": tag, "crowns": 1 if tag == me else 0, "globalRank": rank,
            "cards": [{"id": first + i} for i in range(8)],
        }

    return {
        "type": kind,
        "battleTime": f"20261030T01{minute:02d}00.000Z",
        "leagueNumber": 10,
        "team": [side(me, None, 100)],
        "opponent": [side(opp, opp_rank, 200)],
    }


class FakeClient:
    def __init__(self, logs, errors=None):
        self.logs = logs
        self.errors = errors or {}
        self.calls = []

    def get_battle_log(self, tag):
        self.calls.append(tag)
        if tag in self.errors:
            raise self.errors[tag]
        return self.logs.get(tag, [])


class FakeStore:
    def __init__(self):
        self.battle_ids = set()
        self.crawled = []

    def save_battles(self, battles):
        new = {b.battle_id for b in battles} - self.battle_ids
        self.battle_ids |= new
        return len(new)

    def mark_crawled(self, tag):
        self.crawled.append(tag)


def run(client, seeds, **kw):
    store = FakeStore()
    sleeps = []
    stats = crawl(client, store, seeds, sleep=sleeps.append, **kw)
    return stats, store, sleeps


def test_opponents_are_visited_best_ranked_first():
    logs = {
        "#2PP": [
            ranked_battle("#2PP", "#9QJ", opp_rank=500, minute=1),
            ranked_battle("#2PP", "#8YY", opp_rank=5, minute=2),
            ranked_battle("#2PP", "#LLL", opp_rank=None, minute=3),
        ],
    }
    client = FakeClient(logs)
    stats, _, _ = run(client, [(1, "#2PP")], max_players=10)
    assert client.calls == ["#2PP", "#8YY", "#9QJ", "#LLL"]  # rank 5, then 500, then unranked
    assert stats.discovered == 3


def test_budget_limits_requests():
    logs = {"#2PP": [ranked_battle("#2PP", f"#{t}", opp_rank=i, minute=i)
                     for i, t in enumerate(["9QJ", "8YY", "LLL", "QQQ"])]}
    client = FakeClient(logs)
    stats, _, _ = run(client, [(1, "#2PP")], max_players=2)
    assert len(client.calls) == 2
    assert stats.players_crawled == 2


def test_each_player_fetched_once_and_battles_deduplicated():
    battle_ab = ranked_battle("#2PP", "#9QJ", opp_rank=2)
    flipped = ranked_battle("#9QJ", "#2PP", opp_rank=1)
    flipped["battleTime"] = battle_ab["battleTime"]  # same battle from the other side
    client = FakeClient({"#2PP": [battle_ab], "#9QJ": [flipped]})
    stats, store, _ = run(client, [(1, "#2PP"), (2, "#2PP"), (3, "2pp")], max_players=10)
    assert client.calls == ["#2PP", "#9QJ"]
    assert stats.battles_seen == 2 and stats.battles_new == 1


def test_non_ranked_battles_do_not_expand_the_crawl():
    client = FakeClient({"#2PP": [ranked_battle("#2PP", "#9QJ", kind="PvP")]})
    stats, _, _ = run(client, [(1, "#2PP")], max_players=10)
    assert client.calls == ["#2PP"]
    assert stats.discovered == 0


def test_pause_between_requests():
    client = FakeClient({"#2PP": [ranked_battle("#2PP", "#9QJ", opp_rank=2)]})
    _, _, sleeps = run(client, [(1, "#2PP")], max_players=10, pause=0.3)
    assert sleeps == [0.3]  # one pause between the two requests, none before the first


def test_not_found_is_skipped():
    client = FakeClient({}, errors={"#2PP": NotFoundError("gone", 404)})
    stats, store, _ = run(client, [(1, "#2PP"), (2, "#9QJ")], max_players=10)
    assert stats.not_found == 1
    assert store.crawled == ["#9QJ"]


def test_403_aborts_immediately():
    client = FakeClient({}, errors={"#2PP": ApiError("forbidden", 403)})
    with pytest.raises(CrawlAborted):
        run(client, [(1, "#2PP"), (2, "#9QJ")], max_players=10)
    assert client.calls == ["#2PP"]


def test_repeated_failures_abort():
    tags = ["#2PP", "#9QJ", "#8YY", "#LLL", "#QQQ", "#PPP", "#YYY", "#GGG", "#RRR", "#JJJ", "#CCC"]
    client = FakeClient({}, errors={t: ApiError("boom", 503) for t in tags})
    with pytest.raises(CrawlAborted):
        run(client, [(i, t) for i, t in enumerate(tags)], max_players=50)
    assert len(client.calls) == 10


def test_invalid_seed_tags_are_ignored():
    client = FakeClient({})
    run(client, [(1, "#2PP/../x"), (2, "#9QJ")], max_players=10)
    assert client.calls == ["#9QJ"]


class BoardClient:
    def __init__(self, current, seasons, season_board):
        self.current, self.seasons, self.season_board = current, seasons, season_board
        self.season_requested = None

    def get_ranked_leaderboard(self, location, max_items):
        return self.current

    def get_seasons(self):
        return self.seasons

    def get_ranked_season_leaderboard(self, season_id, max_items):
        self.season_requested = season_id
        return self.season_board


def test_leaderboard_falls_back_to_latest_season_when_empty():
    client = BoardClient([], [{"id": "2026-08"}, {"id": "2026-09"}],
                         [{"tag": "#2PP", "rank": 1}])
    entries, source = fetch_leaderboard(client, location="global", season=None, limit=10)
    assert source == "season" and client.season_requested == "2026-09"
    assert [e.tag for e in entries] == ["#2PP"]


def test_current_leaderboard_used_when_available():
    client = BoardClient([{"tag": "#9QJ", "rank": 1}], [], [])
    entries, source = fetch_leaderboard(client, location="global", season=None, limit=10)
    assert source == "leaderboard" and entries[0].tag == "#9QJ"
