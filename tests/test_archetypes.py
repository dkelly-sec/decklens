import math

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
