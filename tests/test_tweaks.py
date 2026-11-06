from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from decklens.archetypes import ArchetypeModel
from decklens.features import CardVocab, Matchup
from decklens.web.tiers import TierStats
from decklens.web.tweaks import find_tweaks

EVO = 1_000_000_000
CARDS = {"X-Bow": 27000008, "Tesla": 27000006, "Archers": 26000001, "Knight": 26000000,
         "Fireball": 28000000, "The Log": 28000011, "Skeletons": 26000010,
         "Electro Spirit": 26000084, "Rocket": 28000003, "Giant Snowball": 28000017,
         "Ice Wizard": 26000023, "Graveyard": 28000010, "Hog Rider": 26000021,
         "Musketeer": 26000014, "Cannon": 27000000, "Ice Golem": 26000038, "Ice Spirit": 26000030}
ID = CARDS
NAMES = {i: n for n, i in CARDS.items()} | {i + EVO: f"Evo {n}" for n, i in CARDS.items()}
XBOW = ("X-Bow", "Tesla", "Archers", "Knight", "Fireball", "The Log", "Skeletons", "Electro Spirit")
HOG = ("Hog Rider", "Musketeer", "Cannon", "Ice Golem", "Ice Spirit", "Skeletons", "Fireball", "The Log")


def deck(names, swap=None, evo=None):
    names = [swap[1] if swap and n == swap[0] else n for n in names]
    ids = tuple(sorted(ID[n] for n in names))
    return ids + ((ID[evo] + EVO,) if evo else ())


MINE = deck(XBOW)
ROCKET = deck(XBOW, ("Fireball", "Rocket"))
SNOW = deck(XBOW, ("The Log", "Giant Snowball"), evo="Giant Snowball")
ICEWIZ = deck(XBOW, ("Archers", "Ice Wizard"))
OPP = deck(HOG)


@pytest.fixture(scope="module")
def table():
    rng = np.random.default_rng(0)
    now = datetime.now(timezone.utc)
    rows = []

    def games(d, n, rate):
        for k in range(n):
            rating = 2500 + int(rng.integers(0, 600))
            rows.append((0 if rng.random() < rate else 1, d, None, OPP, None,
                         now - timedelta(hours=k % 200), rating, rating))

    games(MINE, 80, 0.50)
    games(ROCKET, 120, 0.68)      # Rocket instead of Fireball: clearly better
    games(SNOW, 60, 0.56)         # Evo Snowball instead of The Log: a bit better
    games(ICEWIZ, 3, 1.0)         # 3-0: too few games to rank
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    train = [Matchup(f"t{k}", start, MINE, OPP, 0.0, 0.0, k % 2) for k in range(40)]
    vocab = CardVocab.build(train + [Matchup("r", start, ROCKET, SNOW, 0.0, 0.0, 1)], names=NAMES)
    model = ArchetypeModel.fit(vocab, train, 2, 20.0)
    return TierStats(model, lambda: rows).table(), vocab


def test_swaps_come_from_real_versions_of_your_deck(table):
    t, vocab = table
    out = find_tweaks(t, vocab, MINE)
    swaps = [(s["out"], s["in"]) for s in out["swaps"]]
    assert swaps[0] == ("Fireball", "Rocket")
    assert ("The Log", "Evo Giant Snowball") in swaps            # the Evo version, by name
    assert ("Archers", "Ice Wizard") not in swaps                # 3 games: not ranked
    assert not any("Graveyard" in s for pair in swaps for s in pair)  # nobody plays it here
    rocket = out["swaps"][0]["bands"]["all"]
    assert rocket["games"] == 120 and rocket["gain"] > 0.1
    assert out["bands"][0]["games"] == 80 and 0.4 < out["bands"][0]["base"] < 0.6


def test_bands_and_sorting(table):
    t, vocab = table
    out = find_tweaks(t, vocab, MINE, sort_band="high")
    assert [b["key"] for b in out["bands"]] == ["all", "low", "mid", "high"]
    gains = [s["bands"]["high"]["gain"] for s in out["swaps"]]
    assert gains == sorted(gains, reverse=True)
    assert all(s["bands"]["high"]["games"] >= 10 for s in out["swaps"])


def test_a_deck_nobody_plays_gets_no_swaps(table):
    t, vocab = table
    # Two cards off every stored deck: no real one-card variant to learn from.
    lonely = deck(deck_names := [n for n in XBOW if n not in ("Fireball", "The Log")]
                  + ["Graveyard", "Ice Wizard"])
    assert len(deck_names) == 8 and find_tweaks(t, vocab, lonely)["swaps"] == []


def test_tweak_route_validates_decks():
    from fastapi.testclient import TestClient

    from decklens.web.app import Services, create_app
    from decklens.web.services import Predictor

    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    train = [Matchup(f"t{k}", start, MINE, OPP, 0.0, 0.0, k % 2) for k in range(40)]
    vocab = CardVocab.build(train, names=NAMES)
    app = TestClient(create_app(Services(client=None,
                                         predictor=Predictor(ArchetypeModel.fit(vocab, train, 2, 20.0)))))
    origin = {"origin": "http://testserver"}
    assert app.post("/api/tweaks", json={"deck": list(MINE)}, headers=origin).status_code == 503
    assert app.post("/api/tweaks", json={"deck": list(MINE)[:5]}, headers=origin).status_code == 400
    assert app.post("/api/tweaks", json={"deck": list(MINE), "band": "x"},
                    headers=origin).status_code == 422
