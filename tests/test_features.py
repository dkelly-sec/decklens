import math
from datetime import datetime, timezone

import numpy as np
import pytest

from decklens.features import (
    UNRANKED_RANK, CardVocab, Matchup, encode_difference, encode_pair, extra_features, flipped,
    level_gap,
)

T = datetime(2026, 10, 30, tzinfo=timezone.utc)


def m(a, b, gap_a=0.0, gap_b=0.0, won=1, bid="x", rank_a=None, rank_b=None):
    return Matchup(bid, T, tuple(a), tuple(b), gap_a, gap_b, won, rank_a, rank_b)


def test_level_gap():
    cards = [{"level": 14, "maxLevel": 16}, {"level": 16, "maxLevel": 16}]
    assert level_gap(cards) == 1.0
    assert level_gap([]) == 0.0
    # malformed or impossible values are ignored, not trusted
    assert level_gap(["x", {"level": "14", "maxLevel": 16}, {"level": 20, "maxLevel": 16}]) == 0.0


def test_vocab_min_count_and_unseen_cards():
    data = [m([1, 2], [2, 3]), m([2, 4], [1, 2])]
    vocab = CardVocab.build(data, min_count=2)
    assert vocab.card_ids == [1, 2]
    assert vocab.one_hot([2, 99]).tolist() == [0.0, 1.0]  # card 99 unseen: ignored


def test_vocab_round_trip():
    vocab = CardVocab([5, 7], {5: "Hog Rider"})
    again = CardVocab.from_dict(vocab.to_dict())
    assert again.card_ids == [5, 7] and again.name(5) == "Hog Rider" and again.name(7) == "7"


def test_difference_encoding_is_antisymmetric():
    vocab = CardVocab([1, 2, 3])
    match = m([1, 2], [2, 3], gap_a=0.5, gap_b=1.5, rank_a=100, rank_b=1000)
    forward = encode_difference(vocab, [match])[0]
    backward = encode_difference(vocab, [flipped(match)])[0]
    # cards, then level (A 1 level better), skill (ln 10: A ranked 10x higher), ranked (both)
    assert forward[:4].tolist() == [1.0, 0.0, -1.0, 1.0]
    assert forward[4] == pytest.approx(math.log(10)) and forward[5] == 0.0
    assert np.allclose(forward, -backward)


def test_pair_encoding_layout():
    vocab = CardVocab([1, 2, 3])
    row = encode_pair(vocab, [m([1], [3], gap_a=2.0, gap_b=0.0)])[0]
    assert row.tolist() == [1, 0, 0, 0, 0, 1, -2.0, 0.0, 0.0]  # unranked vs unranked: no skill gap


def test_flipped_swaps_everything():
    match = m([1], [2], gap_a=0.1, gap_b=0.9, won=1, rank_a=5, rank_b=None)
    f = flipped(match)
    assert (f.deck_a, f.deck_b, f.level_gap_a, f.level_gap_b, f.a_won) == ((2,), (1,), 0.9, 0.1, 0)
    assert (f.rank_a, f.rank_b) == (None, 5)


def test_ranked_player_against_unranked():
    _, skill, ranked = extra_features(m([1], [2], rank_a=150, rank_b=None))
    assert skill == pytest.approx(math.log(UNRANKED_RANK / 150))
    assert ranked == 1.0


@pytest.mark.parametrize("bad", [0, -3, True, "7", 10**9])
def test_invalid_ranks_count_as_unranked(bad):
    assert extra_features(m([1], [2], rank_a=bad, rank_b=None))[1:] == (0.0, 0.0)
