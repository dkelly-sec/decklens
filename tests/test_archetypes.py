import json
import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from sklearn.metrics import adjusted_rand_score

from decklens.archetypes import ArchetypeModel, fit_tuned
from decklens.features import CardVocab, Matchup, flipped, labels
from decklens.model import LinearMatchupModel, evaluate, time_split

from synthetic import N_ARCHETYPES, card_id, core_cards, make_archetype_matchups


@pytest.fixture(scope="module")
def data():
    matchups, names, truth = make_archetype_matchups(6000, seed=3)
    train, test, _ = time_split(matchups, 0.2)
    vocab = CardVocab.build(train, names=names)
    model = ArchetypeModel.fit(vocab, train, n_clusters=N_ARCHETYPES, prior_strength=20)
    return train, test, vocab, truth, model


def test_clustering_recovers_the_planted_archetypes(data):
    _, _, _, truth, model = data
    decks = list(truth)
    found = model.assign(decks)
    assert adjusted_rand_score([truth[d] for d in decks], found) > 0.95


def test_archetypes_are_named_after_their_core_cards(data):
    _, _, _, truth, model = data
    for archetype in range(N_ARCHETYPES):
        cluster = model.assign([core_cards(archetype) + (card_id(40), card_id(41), card_id(42))])[0]
        first = model.names[cluster].split(" · ")[0]
        assert first in {f"Card {c - 26000000}" for c in core_cards(archetype)}


def test_beats_a_per_card_model_on_rock_paper_scissors(data):
    train, test, vocab, _, model = data
    y = labels(test)
    archetype_loss = evaluate("arch", model.predict_proba(test), y).log_loss
    linear_loss = evaluate("lr", LinearMatchupModel.fit(vocab, train).predict_proba(test), y).log_loss
    assert archetype_loss < linear_loss - 0.03
    assert archetype_loss < math.log(2) - 0.03


def test_symmetric_and_mirrors_are_even(data):
    _, test, _, _, model = data
    p = model.predict_proba(test[:100])
    q = model.predict_proba([flipped(m) for m in test[:100]])
    assert np.allclose(p + q, 1.0)
    for i in range(len(model.names)):
        assert model.matchup(i, i)[0] == pytest.approx(0.5)


def test_few_games_are_pulled_towards_the_prior():
    vocab = CardVocab([1, 2, 3, 4])
    centroids = np.eye(2, 4)
    games = np.array([[0, 3], [3, 0]], dtype=float)
    wins = np.array([[0, 3], [0, 0]], dtype=float)  # archetype 0 is 3-0
    model = ArchetypeModel(vocab, centroids, np.array([0, 1]), ["A", "B"], wins, games, 30.0,
                           np.array([3.0, 3.0]))
    p, n = model.matchup(0, 1)
    assert n == 3 and 0.5 < p < 0.7  # a 3-0 start is not read as 100%
    assert model.matchup(1, 0)[0] == pytest.approx(1 - p)


def test_unseen_deck_goes_to_the_nearest_archetype(data):
    _, _, _, _, model = data
    known = model.assign([core_cards(2) + (card_id(40), card_id(41), card_id(42))])[0]
    with_new_cards = core_cards(2) + (99999991, 99999992, 99999993)  # cards never seen
    assert model.assign([with_new_cards])[0] == known


def test_tuning_and_json_round_trip(data):
    train, test, vocab, _, _ = data
    model, info = fit_tuned(vocab, train, cluster_options=(3, 6), prior_options=(5.0, 50.0))
    assert info["archetypes"] in (3, 6) and info["prior_strength"] in (5.0, 50.0)
    again = ArchetypeModel.from_json(model.to_json())
    assert np.allclose(again.predict_proba(test[:50]), model.predict_proba(test[:50]), atol=1e-4)
    assert again.names == model.names


@pytest.mark.parametrize("bad", [
    '{"format": "pickle"}',
    '{"format": "decklens-archetypes-v1", "vocab": {"card_ids": [1, 2]}, "names": ["A"], '
    '"groups": [0], "centroids": [[1.0]], "wins": [[0]], "games": [[0]], "deck_counts": [1], '
    '"prior_strength": 10}',  # centroid has the wrong number of cards
    '{"format": "decklens-archetypes-v1", "vocab": {"card_ids": [1]}, "names": ["A"], '
    '"groups": [0], "centroids": [[1.0]], "wins": [[-5]], "games": [[0]], "deck_counts": [1], '
    '"prior_strength": 10}',  # negative counts
    '{"format": "decklens-archetypes-v1", "vocab": {"card_ids": [1]}, "names": ["A"], '
    '"groups": [3], "centroids": [[1.0]], "wins": [[0]], "games": [[0]], "deck_counts": [1], '
    '"prior_strength": 10}',  # cluster points at an archetype that doesn't exist
])
def test_malformed_files_are_rejected(bad):
    with pytest.raises(ValueError):
        ArchetypeModel.from_json(bad)


def test_split_archetypes_are_merged_so_names_are_unique():
    """Too many clusters split a deck type by its flex cards; same signature means merge."""
    matchups, names, truth = make_archetype_matchups(4000, seed=8)
    train, _, _ = time_split(matchups, 0.2)
    model = ArchetypeModel.fit(CardVocab.build(train, names=names), train, n_clusters=18,
                               prior_strength=20)
    assert len(model.names) == len(set(model.names))
    assert len(model.names) == N_ARCHETYPES  # 18 clusters merge back to the 6 real types
    assert len(model.groups) == 18


def test_tuning_needs_options(data):
    train, _, vocab, _, _ = data
    with pytest.raises(ValueError, match="no archetype"):
        fit_tuned(vocab, train, cluster_options=())


# --- archetypes by win condition, named the way players name them ------------------

REAL_CARDS = {  # name: elixir
    "Golem": 8, "Elixir Collector": 6, "Electro Dragon": 5, "Berserker": 2, "Elite Barbarians": 6,
    "Skeleton Dragons": 4, "Tornado": 3, "The Log": 2, "Night Witch": 4, "Lumberjack": 4,
    "X-Bow": 6, "Tesla": 4, "Archers": 3, "Knight": 3, "Fireball": 4, "Skeletons": 1,
    "Electro Spirit": 1, "Hog Rider": 4, "Musketeer": 4, "Cannon": 3, "Ice Golem": 2,
    "Ice Spirit": 1, "Graveyard": 5, "Poison": 4, "Baby Dragon": 4, "Ice Wizard": 3,
    "Bowler": 5, "Barbarian Barrel": 2, "Valkyrie": 4, "Mortar": 4,
    "Giant": 5, "Zappies": 4, "Arrows": 3, "Miner": 3,
}
REAL_IDS = {name: 26000000 + i for i, name in enumerate(REAL_CARDS)}
REAL_DECKS = {  # archetype: (core cards, flex pool), 8 cards per deck
    "Golem Pump": (["Golem", "Elixir Collector", "Electro Dragon", "Berserker", "Elite Barbarians",
                    "Skeleton Dragons", "Tornado", "The Log"], []),
    "X-Bow 3.0": (["X-Bow", "Tesla", "Archers", "Knight", "Fireball", "The Log", "Skeletons",
                   "Electro Spirit"], []),
    "Hog 2.6": (["Hog Rider", "Musketeer", "Cannon", "Ice Golem", "Ice Spirit", "Skeletons",
                 "Fireball", "The Log"], []),
    # Shares Tornado, Log and Skeleton Dragons-style support with the Golem deck.
    # Two win conditions: named after both, not after its most unusual support card.
    "Giant Graveyard": (["Giant", "Graveyard", "Zappies", "Arrows", "Night Witch", "Miner",
                         "Barbarian Barrel", "Knight"], []),
    "Graveyard Poison": (["Graveyard", "Poison", "Tornado", "Knight"],
                         ["Baby Dragon", "Ice Wizard", "Bowler", "Barbarian Barrel", "Valkyrie",
                          "Skeleton Dragons", "The Log"]),
}


def _real_matchups(n=3000, seed=5, golem_share=0.04):
    rng = np.random.default_rng(seed)
    kinds = list(REAL_DECKS)
    weights = np.array([golem_share] + [(1 - golem_share) / (len(kinds) - 1)] * (len(kinds) - 1))
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)

    def deck(kind):
        core, flex = REAL_DECKS[kind]
        extra = list(rng.choice(flex, 8 - len(core), replace=False)) if flex else []
        return tuple(sorted(REAL_IDS[c] for c in core + extra))

    return [Matchup(f"m{k}", start + timedelta(minutes=k),
                    deck(kinds[rng.choice(len(kinds), p=weights)]),
                    deck(kinds[rng.choice(len(kinds), p=weights)]), 0.0, 0.0, int(rng.random() < 0.5))
            for k in range(n)]


@pytest.fixture(scope="module")
def real():
    vocab = CardVocab.build(_real_matchups(), names={i: n for n, i in REAL_IDS.items()},
                            elixir={REAL_IDS[n]: c for n, c in REAL_CARDS.items()})
    return vocab, ArchetypeModel.fit(vocab, _real_matchups(), n_clusters=3, prior_strength=20)


@pytest.mark.parametrize("kind", ["Golem Pump", "X-Bow 3.0", "Hog 2.6", "Giant Graveyard",
                                  "Graveyard Poison"])
def test_rare_decks_keep_their_own_win_condition_and_name(real, kind):
    _, model = real
    core, flex = REAL_DECKS[kind]
    deck = tuple(REAL_IDS[c] for c in core + flex[:8 - len(core)])
    # Golem is 4% of decks and k=3 is too few clusters for k-means alone to
    # keep it apart from Graveyard: the win condition keeps it apart.
    assert model.names[model.assign([deck])[0]] == kind
    assert model.unseen_wincon(deck) is None


def test_a_win_condition_training_never_saw_is_flagged(real):
    _, model = real
    mortar = tuple(REAL_IDS[c] for c in ["Mortar", "Archers", "Knight", "Fireball", "The Log",
                                          "Skeletons", "Cannon", "Ice Spirit"])
    assert model.unseen_wincon(mortar) == "Mortar"
    model.assign([mortar])  # still gets the closest archetype


def test_win_conditions_survive_json_and_old_files_still_load(real):
    vocab, model = real
    again = ArchetypeModel.from_json(model.to_json())
    assert again.keys == model.keys and again.vocab.elixir == vocab.elixir
    decks = [m.deck_a for m in _real_matchups(50, seed=9)]
    assert (again.assign(decks) == model.assign(decks)).all()
    old = json.loads(model.to_json())
    old["format"] = "decklens-archetypes-v1"
    del old["keys"]
    assert len(ArchetypeModel.from_json(json.dumps(old)).assign(decks)) == 50


def test_win_condition_pairs_dont_explode_the_archetype_count():
    from decklens.archetypes import WIN_CONDITIONS

    rng = np.random.default_rng(1)
    wincons = list(WIN_CONDITIONS[:12])
    names = {i: n for i, n in enumerate(wincons)} | {100 + i: f"Filler {i}" for i in range(30)}
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)

    def deck():
        two = rng.choice(len(wincons), 2, replace=False)
        return tuple(sorted([int(c) for c in two] + [100 + int(c) for c in rng.choice(30, 6, replace=False)]))

    matchups = [Matchup(f"p{k}", start + timedelta(minutes=k), deck(), deck(), 0.0, 0.0, k % 2)
                for k in range(2000)]
    model = ArchetypeModel.fit(CardVocab.build(matchups, names=names), matchups, 10, 20)
    assert len(model.names) <= len(wincons) + 10  # not one per pair (66 pairs here)


def test_a_name_never_names_a_card_the_deck_lacks(real):
    _, model = real
    decks = {m.deck_a for m in _real_matchups(400, seed=11)}
    for deck, label in zip(decks, model.labels(list(decks))):
        named = [REAL_IDS[w] for w in REAL_IDS if w in label and w not in ("Hog Rider",)]
        assert all(c in deck for c in named), (label, deck)
    # Every Graveyard deck in training ran Poison; one without it is just "Graveyard".
    no_poison = tuple(REAL_IDS[c] for c in ["Graveyard", "Tornado", "Knight", "Baby Dragon",
                                             "Ice Wizard", "Bowler", "Valkyrie", "The Log"])
    assert model.labels([no_poison]) == ["Splashyard"]   # what it is: Graveyard with splash


def test_groups_with_the_same_name_are_pooled_not_numbered(real):
    _, model = real
    assert len(set(model.names)) == len(model.names)
    assert not any(name.endswith(")") for name in model.names)


# Real id ranges matter here: spells are 28xxxxxx (Hyperbait means no spell but the Barrel).
PN = {"Hog Rider": 26000021, "Royal Hogs": 26000059, "Earthquake": 28000014,
      "Goblin Barrel": 28000004, "Graveyard": 28000010, "Battle Ram": 26000036,
      "Giant": 26000003, "Sparky": 26000033, "The Log": 28000011, "Barbarian Barrel": 28000015,
      "Tornado": 28000012, "Arrows": 28000001, "Zap": 28000008, "Rage": 28000002,
      "Poison": 28000009, "Baby Dragon": 26000015, "Ice Wizard": 26000023, "Princess": 26000026,
      "Goblin Gang": 26000041, "Dart Goblin": 26000040, "Goblin Hut": 27000001,
      "Lumberjack": 26000035, "Bandit": 26000046, "Magic Archer": 26000062,
      "Royal Ghost": 26000050, "Electro Wizard": 26000042, "Mini P.E.K.K.A": 26000018,
      "Dark Prince": 26000027, "Skeletons": 26000010, "Cannon": 27000000, "Musketeer": 26000014,
      "Ice Spirit": 26000030, "Valkyrie": 26000011, "Electro Spirit": 26000084, "Knight": 26000000}
PLAYER_DECKS = {
    "Hog EQ": ["Hog Rider", "Earthquake", "Skeletons", "Cannon", "Musketeer", "Ice Spirit",
               "Valkyrie", "Electro Spirit"],
    "Pigs EQ": ["Royal Hogs", "Earthquake", "Skeletons", "Cannon", "Musketeer", "Ice Spirit",
                "Valkyrie", "Knight"],
    "Hyperbait": ["Goblin Barrel", "Princess", "Goblin Gang", "Dart Goblin", "Ice Spirit",
                  "Knight", "Cannon", "Skeletons"],
    "Log Bait": ["Goblin Barrel", "The Log", "Princess", "Goblin Gang", "Ice Spirit", "Knight",
                 "Cannon", "Rage"],
    "Splashyard": ["Graveyard", "Baby Dragon", "Ice Wizard", "Knight", "Barbarian Barrel",
                   "Tornado", "Goblin Hut", "Valkyrie"],
    "Bridge Spam": ["Battle Ram", "Lumberjack", "Bandit", "Magic Archer", "Royal Ghost",
                    "Electro Wizard", "Arrows", "Dark Prince"],
    "Giant Sparky": ["Giant", "Sparky", "Mini P.E.K.K.A", "Electro Wizard", "Rage", "Zap",
                     "Goblin Gang", "Dark Prince"],
}


def _pn_deck(names):
    return tuple(sorted(PN[n] for n in names))


def test_names_players_actually_use():
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    kinds = list(PLAYER_DECKS)
    rng = np.random.default_rng(2)
    matchups = [Matchup(f"p{k}", start, _pn_deck(PLAYER_DECKS[kinds[rng.integers(len(kinds))]]),
                        _pn_deck(PLAYER_DECKS[kinds[rng.integers(len(kinds))]]), 0.0, 0.0, k % 2)
                for k in range(1200)]
    vocab = CardVocab.build(matchups, names={i: n for n, i in PN.items()})
    model = ArchetypeModel.fit(vocab, matchups, 6, 20.0)
    for kind, cards in PLAYER_DECKS.items():
        assert model.labels([_pn_deck(cards)]) == [kind]
    # A Hyperbait list with The Log in it isn't Hyperbait any more: it's Log Bait.
    with_log = [c if c != "Dart Goblin" else "The Log" for c in PLAYER_DECKS["Hyperbait"]]
    assert model.labels([_pn_deck(with_log)]) == ["Log Bait"]
    # Any other spell makes it Log Bait too.
    with_zap = [c if c != "Dart Goblin" else "Zap" for c in PLAYER_DECKS["Hyperbait"]]
    assert model.labels([_pn_deck(with_zap)]) == ["Log Bait"]
    # Graveyard with Poison is a Poison deck, not Splashyard.
    poison_yard = [c if c != "Tornado" else "Poison" for c in PLAYER_DECKS["Splashyard"]]
    assert model.labels([_pn_deck(poison_yard)]) != ["Splashyard"]
