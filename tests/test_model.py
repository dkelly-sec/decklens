import math

import numpy as np
import pytest

from decklens.features import CardVocab, flipped, labels
from decklens.model import (
    LinearMatchupModel, TreeMatchupModel, evaluate, time_split, wilson_interval,
)

from synthetic import CONFOUND_CARD, COUNTER, COUNTERED, STRONG_CARD, make_matchups


@pytest.fixture(scope="module")
def data():
    matchups, names, strengths = make_matchups(6000)
    train, test, cutoff = time_split(matchups, 0.2)
    vocab = CardVocab.build(train, names=names)
    return train, test, vocab, strengths


@pytest.fixture(scope="module")
def linear(data):
    train, _, vocab, _ = data
    return LinearMatchupModel.fit(vocab, train)


def test_time_split_trains_on_past_tests_on_future(data):
    train, test, _, _ = data
    assert max(m.battle_time for m in train) < min(m.battle_time for m in test)
    assert not {m.battle_id for m in train} & {m.battle_id for m in test}
    assert len(test) == 1200


def test_wilson_interval():
    lo, hi = wilson_interval(60, 100)
    assert 0.50 < lo < 0.51 and 0.69 < hi < 0.70
    assert wilson_interval(0, 0) == (0.0, 1.0)


def test_coin_flip_scores():
    e = evaluate("coin", np.full(4, 0.5), np.array([1, 0, 1, 0]))
    assert math.isclose(e.log_loss, math.log(2)) and e.brier == 0.25


def test_linear_model_recovers_planted_strongest_card(linear):
    strongest_name, _ = linear.card_strengths()[0]
    assert strongest_name == linear.vocab.name(STRONG_CARD)
    assert linear.level_weight > 0  # higher levels help, as planted
    assert linear.skill_weight > 0  # better-ranked players win more, as planted


def test_linear_model_beats_coin_flip(data, linear):
    _, test, _, _ = data
    e = evaluate("lr", linear.predict_proba(test), labels(test))
    assert e.accuracy_ci[0] > 0.5  # whole 95% range above a coin flip
    assert e.log_loss < math.log(2)


def test_linear_model_is_symmetric(data, linear):
    _, test, _, _ = data
    p = linear.predict_proba(test[:50])
    q = linear.predict_proba([flipped(m) for m in test[:50]])
    assert np.allclose(p + q, 1.0)

    from decklens.features import Matchup
    t = test[0]
    mirror = Matchup(t.battle_id, t.battle_time, t.deck_a, t.deck_a, 0.0, 0.0, 1, 50, 50)
    assert linear.predict_proba([mirror])[0] == pytest.approx(0.5)  # identical decks: 50/50


def test_tree_model_is_symmetric_and_learns_the_counter(data):
    train, test, vocab, _ = data
    trees = TreeMatchupModel.fit(vocab, train)
    p = trees.predict_proba(test[:50])
    q = trees.predict_proba([flipped(m) for m in test[:50]])
    assert np.allclose(p + q, 1.0)

    from decklens.features import Matchup
    base = tuple(range(26000010, 26000017))
    t = test[0].battle_time
    with_counter = Matchup("c1", t, base + (COUNTER,), base + (COUNTERED,), 0.0, 0.0, 1)
    assert trees.predict_proba([with_counter])[0] > 0.5


def test_json_export_round_trip(data, linear):
    _, test, _, _ = data
    again = LinearMatchupModel.from_json(linear.to_json())
    assert np.allclose(again.predict_proba(test[:20]), linear.predict_proba(test[:20]), atol=1e-4)


@pytest.mark.parametrize("bad", [
    '{"format": "pickle"}',
    '{"format": "decklens-linear-v1", "vocab": {"card_ids": [1]}, '
    '"card_weights": [0.1], "level_weight": 0}',  # older format: must be retrained
    '{"format": "decklens-linear-v2", "vocab": {"card_ids": [1]}, "card_weights": [NaN], '
    '"extra_weights": {"level": 0, "skill": 0, "ranked": 0}}',
])
def test_json_import_rejects_bad_files(bad):
    with pytest.raises(ValueError):
        LinearMatchupModel.from_json(bad)


def test_explain_names_the_cards_driving_a_prediction(linear):
    deck_a = (STRONG_CARD,) + tuple(range(26000020, 26000027))
    deck_b = tuple(range(26000030, 26000038))
    top = linear.explain(deck_a, deck_b, top=3)
    assert top[0][0] == f"{linear.vocab.name(STRONG_CARD)} (A)" and top[0][1] > 0


def test_skill_feature_removes_the_confound():
    """Skilled players favour a useless card. Without skill data the model credits
    the card for their wins; with skill data that false strength mostly disappears."""
    from dataclasses import replace

    matchups, names, _ = make_matchups(12000, seed=5, skill_weight=0.6)
    no_skill = [replace(m, rank_a=None, rank_b=None) for m in matchups]

    def confound_weight(data):
        train, _, _ = time_split(data, 0.2)
        model = LinearMatchupModel.fit(CardVocab.build(train, names=names), train)
        return dict(zip(model.vocab.card_ids, model.card_weights))[CONFOUND_CARD]

    blind, aware = confound_weight(no_skill), confound_weight(matchups)
    assert blind > 0.15            # fooled: looks like a good card
    assert abs(aware) < blind / 2  # with skill accounted for, most of that is gone
