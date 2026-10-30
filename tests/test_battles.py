import copy

from decklens.battles import make_battle_id, parse_battle, parse_battle_log


def side(tag, name, crowns, first_card_id):
    return {
        "tag": tag,
        "name": name,
        "crowns": crowns,
        "startingTrophies": 9000,
        "clan": {"tag": "#2QQ", "name": "Test Clan"},
        "cards": [{"id": first_card_id + i, "name": f"Card {i}", "level": 14} for i in range(8)],
    }


def battle(team_tag="#2PP", opp_tag="#9QJ", team_crowns=3, opp_crowns=1, kind="pathOfLegend"):
    return {
        "type": kind,
        "battleTime": "20261030T011500.000Z",
        "gameMode": {"id": 1, "name": "Ranked1v1"},
        "team": [side(team_tag, "Alice", team_crowns, 26000000)],
        "opponent": [side(opp_tag, "Bob", opp_crowns, 26000100)],
    }


def flipped(raw):
    out = copy.deepcopy(raw)
    out["team"], out["opponent"] = out["opponent"], out["team"]
    return out


def test_same_battle_from_both_logs_is_identical():
    raw = battle()
    assert parse_battle(raw) == parse_battle(flipped(raw))


def test_battle_id_is_order_independent():
    t = "20261030T011500.000Z"
    assert make_battle_id(t, "#2PP", "#9QJ") == make_battle_id(t, "#9QJ", "#2PP")


def test_winner_and_canonical_order():
    b = parse_battle(battle(team_tag="#9QJ", opp_tag="#2PP", team_crowns=0, opp_crowns=2))
    assert [p.tag for p in b.players] == ["#2PP", "#9QJ"]
    assert b.players[b.winner].tag == "#2PP"


def test_draw_has_no_winner():
    assert parse_battle(battle(team_crowns=1, opp_crowns=1)).winner is None


def test_deck_is_sorted_card_ids():
    b = parse_battle(battle())
    assert all(list(p.card_ids) == sorted(p.card_ids) for p in b.players)
    assert all(len(p.card_ids) == 8 for p in b.players)


def test_non_kept_types_and_2v2_are_skipped():
    two_v_two = battle()
    two_v_two["team"].append(side("#8YY", "Carol", 3, 26000200))
    entries = [battle(), battle(kind="clanMate"), two_v_two]
    kept, skipped = parse_battle_log(entries)
    assert len(kept) == 1
    assert skipped == {"clanMate": 1, "pathOfLegend": 1}


def test_malformed_entries_are_skipped_not_trusted():
    bad_tag = battle(team_tag="#2PP/../x")
    short_deck = battle()
    short_deck["team"][0]["cards"] = short_deck["team"][0]["cards"][:5]
    bad_time = battle()
    bad_time["battleTime"] = "yesterday"
    kept, skipped = parse_battle_log([bad_tag, short_deck, bad_time, "not a dict"])
    assert kept == []
    assert sum(skipped.values()) == 4


def test_long_names_are_truncated():
    raw = battle()
    raw["team"][0]["name"] = "x" * 500
    assert all(len(p.name) <= 64 for p in parse_battle(raw).players)
