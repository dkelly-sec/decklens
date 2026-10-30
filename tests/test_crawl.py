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


def test_season_fallback_skips_placeholder_seasons():
    seasons = [{"code": "2026-08"}, {"code": "2026-09"},
               {"code": None, "uniqueId": None, "endTime": None},
               {"code": None, "uniqueId": None, "endTime": None}]
    client = BoardClient([], seasons, [{"tag": "#2PP", "rank": 1}])
    entries, source = fetch_leaderboard(client, location="global", season=None, limit=10)
    assert client.season_requested == "2026-09" and source == "season"


def test_season_fallback_with_only_placeholders_returns_nothing():
    client = BoardClient([], [{"code": None}, "junk"], [{"tag": "#2PP"}])
    entries, _ = fetch_leaderboard(client, location="global", season=None, limit=10)
    assert entries == [] and client.season_requested is None


# --- country leaderboards (name index) ---------------------------------------------------


class CountryClient:
    def __init__(self, boards, errors=None, locations=None):
        self.boards, self.errors, self.requested = boards, errors or {}, []
        self.locations = locations if locations is not None else [
            {"id": 57000001, "name": "Europe", "isCountry": False},      # region: skipped
            {"id": 57000017, "name": "Australia", "isCountry": True},
            {"id": 57000056, "name": "France", "isCountry": True},
            {"id": "57000099", "isCountry": True},                       # id not an int: skipped
            {"id": 57000094, "isCountry": "yes"},                        # flag not True: skipped
            "junk",
        ]

    def get_locations(self):
        return self.locations

    def get_ranked_leaderboard(self, location, max_items):
        self.requested.append((location, max_items))
        if location in self.errors:
            raise self.errors[location]
        return self.boards.get(location, [])


def sweep(client, **kw):
    from decklens.crawl import sweep_countries

    saved, sleeps = [], []
    stats = sweep_countries(client, lambda found: saved.extend(found) or len(found),
                            per_country=kw.pop("per_country", 1000), sleep=sleeps.append, **kw)
    return stats, saved, sleeps


def test_country_boards_fill_the_name_index_without_local_ranks():
    client = CountryClient({"57000017": [{"tag": "#2PP", "name": "Aussie", "rank": 1,
                                          "eloRating": 2400}],
                            "57000056": [{"tag": "#9QJ", "name": "Français", "rank": 3}]})
    stats, saved, sleeps = sweep(client, per_country=500)
    assert client.requested == [("57000017", 500), ("57000056", 500)]  # countries only
    assert {e.tag for e in saved} == {"#2PP", "#9QJ"}
    assert all(e.rank is None for e in saved)          # country ranks never stored as global
    assert (stats.countries, stats.with_players, stats.players, stats.failures) == (2, 2, 2, 0)
    assert len(sleeps) == 2                             # pause after every request


def test_a_failing_country_is_skipped_but_403_stops_the_sweep():
    client = CountryClient({"57000056": [{"tag": "#9QJ", "name": "x", "rank": 1}]},
                           errors={"57000017": ApiError("HTTP 500", 500)})
    stats, saved, _ = sweep(client)
    assert stats.failures == 1 and [e.tag for e in saved] == ["#9QJ"]

    with pytest.raises(CrawlAborted):
        sweep(CountryClient({}, errors={"57000017": ApiError("403 forbidden", 403)}))


def test_country_sweep_gives_up_when_every_board_fails():
    locations = [{"id": 57000000 + i, "isCountry": True} for i in range(20)]
    errors = {str(57000000 + i): NotFoundError("not found", 404) for i in range(20)}
    stats, saved, _ = sweep(CountryClient({}, errors=errors, locations=locations))
    assert stats.failures == 20 and saved == []          # 404s: endpoint missing, keep going
    errors = {str(57000000 + i): ApiError("HTTP 502", 502) for i in range(20)}
    with pytest.raises(CrawlAborted):
        sweep(CountryClient({}, errors=errors, locations=locations))
