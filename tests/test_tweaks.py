from datetime import datetime, timedelta, timezone

import pytest

from decklens.archetypes import ArchetypeModel
from decklens.features import CardVocab
from decklens.model import TreeMatchupModel, time_split
from decklens.web.tiers import TierStats
from decklens.web.tweaks import candidates, find_tweaks

from synthetic import STRONG_CARD, make_matchups

EVO = 1_000_000_000


@pytest.fixture(scope="module")
def setup():
    matchups, names, _ = make_matchups(6000, seed=1)
    train, _, _ = time_split(matchups, 0.2)
    vocab = CardVocab.build(train, names=names)
    trees = TreeMatchupModel.fit(vocab, train)
    archetypes = ArchetypeModel.fit(vocab, train, 6, 20.0)
    now = datetime.now(timezone.utc)
    rows = [(1 - m.a_won, m.deck_a, None, m.deck_b, None, now - timedelta(hours=k % 100),
             1800 + k % 600, 1800 + (k * 7) % 600) for k, m in enumerate(matchups[:1500])]
    table = TierStats(archetypes, lambda: rows).table()
    return trees, table, archetypes.names


def test_the_planted_best_card_is_the_top_swap_in_every_band(setup, monkeypatch):
    from decklens.web import tweaks

    # The planted card is rarely played here, so widen the pool to be sure it's tried.
    monkeypatch.setattr(tweaks, "CARDS_PER_KIND", 60)
    trees, table, names = setup
    deck = tuple(26000000 + i for i in range(10, 18))       # no STRONG_CARD
    out = find_tweaks(trees, table, names, deck)
    assert [b["key"] for b in out["bands"]] == ["all", "low", "mid", "high"]
    assert all(0 < b["base"] < 1 for b in out["bands"])
    top = out["swaps"][0]
    assert top["in_id"] == STRONG_CARD
    assert all(g > 0 for g in top["gain"].values()) and len(top["vs"]) == 3
    gains = [s["gain"]["all"] for s in out["swaps"]]
    assert gains == sorted(gains, reverse=True)
    by_high = find_tweaks(trees, table, names, deck, sort_band="high")["swaps"]
    assert [s["gain"]["high"] for s in by_high] == sorted((s["gain"]["high"] for s in by_high),
                                                         reverse=True)


def test_swaps_keep_kinds_and_drop_the_form_of_a_swapped_out_card():
    spell, troop, other_spell, other_troop = 28000000, 26000001, 28000005, 26000009
    deck = (spell, troop, 26000002, 26000003, 26000004, 26000005, 26000006, 26000007, troop + EVO)
    swaps = candidates(deck, [other_spell, other_troop])
    pairs = {(old, new) for old, new, _ in swaps}
    assert (spell, other_spell) in pairs and (spell, other_troop) not in pairs   # spell for spell
    assert (troop, other_troop) in pairs and (troop, other_spell) not in pairs
    new_deck = next(d for old, new, d in swaps if old == troop)
    assert troop + EVO not in new_deck and len(new_deck) == 8                    # its Evo goes too
    kept = next(d for old, new, d in swaps if old == spell)
    assert kept[-1] == troop + EVO                                               # others keep theirs


def test_tweak_route_needs_trees_and_valid_decks(setup):
    from fastapi.testclient import TestClient

    from decklens.web.app import Services, create_app
    from decklens.web.services import Predictor

    trees, _, _ = setup
    matchups, vocab_names, _ = make_matchups(2000, seed=3)
    vocab = CardVocab.build(matchups, names=vocab_names)
    p = Predictor(ArchetypeModel.fit(vocab, matchups, 4, 20.0))
    app = TestClient(create_app(Services(client=None, predictor=p)))
    deck = [26000000 + i for i in range(10, 18)]
    origin = {"origin": "http://testserver"}
    assert app.post("/api/tweaks", json={"deck": deck}, headers=origin).status_code == 503
    p.trees = trees
    assert app.post("/api/tweaks", json={"deck": deck[:5]}, headers=origin).status_code == 400
    assert app.post("/api/tweaks", json={"deck": deck, "band": "x"}, headers=origin).status_code == 422
