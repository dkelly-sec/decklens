from decklens.rankings import parse_leaderboard, parse_profile_rank


def test_leaderboard_entries_are_parsed():
    items = [
        {"tag": "#2PP", "name": "Top", "eloRating": 2700, "rank": 1,
         "clan": {"tag": "#9QJ", "name": "Clan"}},
        {"tag": "9qj", "name": "Second", "rank": 2},
    ]
    a, b = parse_leaderboard(items)
    assert (a.tag, a.rating, a.rank, a.clan_name) == ("#2PP", 2700, 1, "Clan")
    assert (b.tag, b.rating, b.rank, b.clan_tag) == ("#9QJ", None, 2, None)


def test_malformed_and_duplicate_entries_are_skipped():
    items = [
        {"tag": "#2PP/../x", "rank": 1},     # invalid tag
        {"tag": "#2PP", "rank": "1; DROP"},  # rank that isn't an integer
        {"tag": "#2PP", "rank": 2},          # duplicate of the previous tag
        "not a dict",
        {"tag": "#9QJ", "rank": True},       # booleans are not ranks
    ]
    entries = parse_leaderboard(items)
    assert [e.tag for e in entries] == ["#2PP", "#9QJ"]
    assert entries[0].rank is None and entries[1].rank is None


def test_long_names_are_truncated():
    (entry,) = parse_leaderboard([{"tag": "#2PP", "name": "x" * 500}])
    assert len(entry.name) == 64


def test_profile_rank():
    profile = {
        "tag": "#2PP", "name": "Me",
        "currentPathOfLegendSeasonResult": {"leagueNumber": 10, "trophies": 2410, "rank": 812},
    }
    entry = parse_profile_rank(profile)
    assert (entry.rating, entry.rank) == (2410, 812)


def test_profile_without_ranked_result():
    entry = parse_profile_rank({"tag": "#2PP", "name": "Me"})
    assert entry.rating is None and entry.rank is None
    assert parse_profile_rank({"tag": "bad tag"}) is None
