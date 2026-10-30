import pytest

from decklens.features import Matchup
from decklens.train import MIN_BATTLES, render_report, run_training

from synthetic import make_matchups


@pytest.fixture(scope="module")
def result():
    matchups, names, _ = make_matchups(5000, seed=1)
    return run_training(matchups, names, min_card_count=10)


def test_models_are_compared_against_baselines(result):
    by_name = {e.name.split(" (")[0]: e for e in result.evaluations}
    assert set(by_name) == {"Coin flip", "Card levels only", "Player skill only",
                            "Logistic regression", "Gradient-boosted trees",
                            "Archetype matchup table"}
    for baseline in ("Coin flip", "Card levels only", "Player skill only"):
        assert by_name["Logistic regression"].log_loss < by_name[baseline].log_loss
    assert 0.3 < result.both_ranked < 0.7  # synthetic data: 70% of players ranked


def test_vocab_comes_from_training_data_only():
    matchups, names, _ = make_matchups(3000, seed=2)
    # A card that only ever appears in the newest battles (the test set)
    late = matchups[-1]
    newcomer = 26999999
    matchups[-1] = Matchup(late.battle_id, late.battle_time, late.deck_a[:7] + (newcomer,),
                           late.deck_b, late.level_gap_a, late.level_gap_b, late.a_won)
    assert matchups[-1].rank_a is None  # rebuilt without ranks: still valid
    r = run_training(matchups, names, min_card_count=1, with_trees=False, with_archetypes=False)
    assert newcomer not in r.model.vocab.index


def test_too_little_data_is_refused():
    matchups, names, _ = make_matchups(MIN_BATTLES - 1)
    with pytest.raises(ValueError, match="at least"):
        run_training(matchups, names)


def test_report_has_every_section(result):
    report = render_report(result)
    for heading in ("## Data", "## Results on the test set", "## Calibration",
                    "## Strongest and weakest cards at equal skill", "## Limits",
                    "Do decks matter beyond skill?", "ranked 10x higher",
                    "## Deck archetypes", "## Most lopsided archetype matchups"):
        assert heading in report
    assert "strictly later than all training battles" in report
    assert "Card 0" in report  # the planted strongest card is listed


def test_archetypes_are_found_and_reported_on_archetype_data():
    from synthetic import make_archetype_matchups

    matchups, names, _ = make_archetype_matchups(6000, seed=4)
    r = run_training(matchups, names, min_card_count=10, with_trees=False)
    by_name = {e.name: e for e in r.evaluations}
    assert by_name["Archetype matchup table"].log_loss < by_name[
        "Logistic regression (cards + levels + skill)"].log_loss
    report = render_report(r)
    lopsided = report.split("## Most lopsided archetype matchups")[1].split("## Limits")[0]
    assert "| Card" in lopsided  # the planted rock-paper-scissors edges show up


def _table_model(pair_wins):
    """Five archetypes, every pair played 100 times; pair_wins[(i, j)] = wins of i over j."""
    import numpy as np

    from decklens.archetypes import ArchetypeModel
    from decklens.features import CardVocab

    k = 5
    wins, games = np.zeros((k, k)), np.zeros((k, k))
    for i in range(k):
        for j in range(i + 1, k):
            w = pair_wins.get((i, j), 50)
            wins[i, j], wins[j, i] = w, 100 - w
            games[i, j] = games[j, i] = 100
    return ArchetypeModel(CardVocab(range(k)), np.eye(k), np.arange(k), list("ABCDE"),
                          wins, games, 20.0, np.full(k, 100.0))


def test_lopsided_list_is_corrected_for_many_pairings():
    from decklens.model import wilson_interval
    from decklens.train import lopsided_matchups

    # A 60-40 record over 100 games clears a plain 95% range...
    assert wilson_interval(60, 100)[0] > 0.5
    model = _table_model({(0, 1): 60, (2, 3): 80})
    found, tested, confidence = lopsided_matchups(model)
    # ...but not once 10 pairings are tested at the same time. 80-20 still does.
    assert tested == 10 and confidence == pytest.approx(0.995)
    assert [(row["a"], row["b"]) for row, _, _ in found] == [("C", "D")]


def test_deck_verdict_only_claims_what_the_range_supports():
    from decklens.model import PairedGain
    from decklens.train import deck_verdict

    assert deck_verdict(PairedGain("Trees", 0.003, 0.001, 0.005, 3000)).startswith("**Yes.**")
    assert deck_verdict(PairedGain("LR", 0.0001, -0.0009, 0.0011, 3000)).startswith(
        "**Not clearly.**")
    assert deck_verdict(PairedGain("LR", -0.004, -0.006, -0.002, 3000)).startswith("**No.**")


def test_report_answers_with_the_best_deck_model():
    from synthetic import make_archetype_matchups

    matchups, names, _ = make_archetype_matchups(5000, seed=6)
    r = run_training(matchups, names, min_card_count=10, with_trees=False)
    assert r.deck_vs_skill.model == "Archetype matchup table"
    assert "**Do decks matter beyond skill?** **Yes.**" in render_report(r)
    assert "Bonferroni" in render_report(r)
