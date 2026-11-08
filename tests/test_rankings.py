from decklens.rankings import current_season_result, parse_leaderboard, parse_profile_rank


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


def test_last_seasons_rank_is_not_this_seasons():
    # Early in a season the API repeats last season's final result as "current".
    profile = {
        "tag": "#2PP", "name": "Me",
        "currentPathOfLegendSeasonResult": {"leagueNumber": 10, "trophies": 2410, "rank": 7853},
        "lastPathOfLegendSeasonResult": {"leagueNumber": 10, "trophies": 2410, "rank": 7853},
    }
    entry = parse_profile_rank(profile)
    assert entry.rating is None and entry.rank is None
    assert current_season_result(profile) == {"leagueNumber": 10}

    from decklens.web.services import summarise_player
    s = summarise_player(profile)
    assert s.get("rank") is None and s.get("rating") is None

    # A real new rank (different from last season's) is kept.
    profile["currentPathOfLegendSeasonResult"] = {"leagueNumber": 10, "trophies": 2100, "rank": 9100}
    assert parse_profile_rank(profile).rank == 9100


def test_below_ultimate_champion_has_no_rank_or_medals():
    # League 5 this season, but the API still carries last season's rank and rating.
    profile = {
        "tag": "#2PP", "name": "Me",
        "currentPathOfLegendSeasonResult": {"leagueNumber": 5, "trophies": 2410, "rank": 7853},
        "lastPathOfLegendSeasonResult": {"leagueNumber": 7, "trophies": 2600, "rank": 5120},
    }
    assert current_season_result(profile) == {"leagueNumber": 5}

    from decklens.web.services import summarise_player
    s = summarise_player(profile)
    assert (s["league"], s["ultimate"], s["rank"], s["rating"]) == (5, False, None, None)


def test_ultimate_champion_rank_only_in_top_10k():
    from decklens.web.services import summarise_player
    profile = {"tag": "#2PP", "name": "Me",
               "currentPathOfLegendSeasonResult": {"leagueNumber": 7, "trophies": 2150, "rank": 9999}}
    s = summarise_player(profile)
    assert (s["ultimate"], s["rating"], s["rank"]) == (True, 2150, 9999)
    profile["currentPathOfLegendSeasonResult"]["rank"] = 10_001
    s = summarise_player(profile)
    assert (s["ultimate"], s["rating"], s["rank"]) == (True, 2150, None)


def test_country_from_clan_and_rank_on_board():
    from decklens.web.services import clan_country, rank_on_board
    assert clan_country({"location": {"id": 57000017, "name": "Australia", "isCountry": True}}) == (
        57000017, "Australia")
    assert clan_country({"location": {"id": 57000006, "name": "International", "isCountry": False}}) is None
    assert clan_country({}) is None
    board = [{"tag": "#9QJ", "name": "A", "rank": 1}, {"tag": "#2PP", "name": "Me", "rank": 35}]
    assert rank_on_board("#2pp", board) == 35
    assert rank_on_board("#8YY", board) is None
