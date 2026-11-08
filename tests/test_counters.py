from decklens.archetypes import WIN_CONDITIONS
from decklens.counters import COUNTERS, counters_in


def test_counter_list_is_well_formed():
    for win_condition, cards in COUNTERS.items():
        assert win_condition in WIN_CONDITIONS  # a typo would never match a deck
        assert cards and len(set(cards)) == len(cards) and win_condition not in cards
        assert all(isinstance(c, str) and c.strip() == c and c for c in cards)


def test_counters_in_finds_the_defenders_counters():
    xbow = ["X-Bow", "Tesla", "Archers", "Knight", "The Log", "Fireball", "Skeletons", "Electro Spirit"]
    recruits = ["Royal Recruits", "Elixir Golem", "Baby Dragon", "Skeleton King",
                "Lightning", "Arrows", "Goblin Curse", "Archers"]
    assert counters_in(xbow, recruits) == [
        {"win_condition": "X-Bow", "cards": ["Arrows", "Lightning", "Royal Recruits"]}]
    assert counters_in(recruits, xbow) == []  # no list for Royal Recruits or Elixir Golem
    assert counters_in(xbow, ["Hog Rider", "Musketeer"]) == []
