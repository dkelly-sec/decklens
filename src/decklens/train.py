"""Train and evaluate matchup models on stored ranked battles.

    python -m decklens.train                   # last 14 days of ranked battles
    python -m decklens.train --days 7          # current meta only
    python -m decklens.train --days 0          # everything stored
    python -m decklens.train --min-league 7    # top league only

Writes reports/model-YYYY-MM-DD.md (aggregate numbers only, safe to commit)
and models/decklens-linear-YYYY-MM-DD.json (plain JSON weights; models/ is
gitignored).
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence

import numpy as np

from .config import ConfigError, load_settings
from .features import CardVocab, Matchup, labels
from .model import (
    Evaluation, ExtrasOnlyBaseline, LinearMatchupModel, TreeMatchupModel, evaluate, time_split,
)

log = logging.getLogger("decklens.train")

MIN_BATTLES = 1000
COIN, LEVELS, SKILL = "Coin flip (always 50%)", "Card levels only", "Player skill only"
LINEAR = "Logistic regression (cards + levels + skill)"
TREES = "Gradient-boosted trees (card pairs + levels + skill)"


@dataclass
class TrainingResult:
    model: LinearMatchupModel
    evaluations: list[Evaluation]
    n_train: int
    n_test: int
    first: datetime
    cutoff: datetime
    last: datetime
    n_cards: int
    min_card_count: int
    min_league: int | None
    days: int | None
    test_side0_win_rate: float
    both_ranked: float  # share of battles where both players had a leaderboard rank
    one_ranked: float


def run_training(
    matchups: Sequence[Matchup],
    names: dict[int, str],
    *,
    test_fraction: float = 0.2,
    min_card_count: int = 30,
    min_league: int | None = None,
    days: int | None = None,
    with_trees: bool = True,
) -> TrainingResult:
    if len(matchups) < MIN_BATTLES:
        raise ValueError(f"need at least {MIN_BATTLES} decided battles, have {len(matchups)}")

    train, test, cutoff = time_split(matchups, test_fraction)
    vocab = CardVocab.build(train, min_count=min_card_count, names=names)  # training data only
    y_test = labels(test)

    evaluations = [evaluate(COIN, np.full(len(test), 0.5), y_test)]
    for name, cols in ((LEVELS, ("level",)), (SKILL, ("skill", "ranked"))):
        baseline = ExtrasOnlyBaseline(cols).fit(train)
        evaluations.append(evaluate(name, baseline.predict_proba(test), y_test))

    linear = LinearMatchupModel.fit(vocab, train)
    linear.trained_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    evaluations.append(evaluate(LINEAR, linear.predict_proba(test), y_test))

    if with_trees:
        trees = TreeMatchupModel.fit(vocab, train)
        evaluations.append(evaluate(TREES, trees.predict_proba(test), y_test))

    ranked_counts = [(m.rank_a is not None) + (m.rank_b is not None) for m in matchups]
    return TrainingResult(
        model=linear, evaluations=evaluations, n_train=len(train), n_test=len(test),
        first=min(m.battle_time for m in train), cutoff=cutoff,
        last=max(m.battle_time for m in test), n_cards=len(vocab),
        min_card_count=min_card_count, min_league=min_league, days=days,
        test_side0_win_rate=float(y_test.mean()),
        both_ranked=ranked_counts.count(2) / len(matchups),
        one_ranked=ranked_counts.count(1) / len(matchups),
    )


def _pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def _ten_times_better(skill_weight: float) -> float:
    """Win chance for a player ranked 10x higher, all else equal (e.g. #100 vs #1,000)."""
    return 1.0 / (1.0 + math.exp(-skill_weight * math.log(10)))


def render_report(r: TrainingResult) -> str:
    fmt = "%Y-%m-%d %H:%M UTC"
    by_name = {e.name: e for e in r.evaluations}
    lines = [
        f"# DeckLens model report ({r.cutoff:%Y-%m-%d})",
        "",
        "Predicts which ranked deck wins a 1v1 battle from the two decks' cards, card levels "
        "and how highly each player was ranked at the time. Player identity is never used.",
        "",
        "## Data",
        "",
        f"- Window: {'last ' + str(r.days) + ' days' if r.days else 'all stored battles'}",
        f"- Training: {r.n_train:,} battles, {r.first:{fmt}} to {r.cutoff:{fmt}}",
        f"- Test: {r.n_test:,} battles, {r.cutoff:{fmt}} to {r.last:{fmt}} "
        "(strictly later than all training battles)",
        f"- League filter: {'league ' + str(r.min_league) + '+' if r.min_league else 'none'}",
        f"- Cards modelled: {r.n_cards} (seen at least {r.min_card_count} times in training)",
        f"- Skill data: both players ranked in {_pct(r.both_ranked)} of battles, "
        f"one player in {_pct(r.one_ranked)}; the rest count as equally unranked",
        f"- Test battles won by side A: {_pct(r.test_side0_win_rate)} (sides are assigned "
        "by tag order, so this should be near 50%)",
        "",
        "## Results on the test set",
        "",
        "| Model | Accuracy (95% range) | Log loss | Brier |",
        "| --- | --- | --- | --- |",
    ]
    for e in r.evaluations:
        lo, hi = e.accuracy_ci
        lines.append(f"| {e.name} | {_pct(e.accuracy)} ({_pct(lo)}–{_pct(hi)}) | "
                     f"{e.log_loss:.4f} | {e.brier:.4f} |")
    lines += [
        "",
        "Lower log loss and Brier are better. A coin flip scores 0.6931 log loss and 0.25 Brier.",
    ]
    if SKILL in by_name and LINEAR in by_name:
        gain = by_name[SKILL].log_loss - by_name[LINEAR].log_loss
        verdict = (f"Yes: adding the decks to skill lowers log loss by {gain:.4f}."
                   if gain > 0 else
                   f"Not measurably: adding the decks to skill doesn't lower log loss "
                   f"(change {-gain:+.4f}).")
        lines += ["", f"**Do decks matter beyond skill?** {verdict} (Logistic regression "
                      "compared with the skill-only baseline.)"]
    lines += [
        "",
        "## Calibration (logistic regression)",
        "",
        "When the model says X%, deck A should win about X% of the time.",
        "",
        "| Predicted | Actual | Battles |",
        "| --- | --- | --- |",
    ]
    for predicted, actual, count in by_name[LINEAR].calibration:
        lines.append(f"| {_pct(predicted)} | {_pct(actual)} | {count:,} |")

    strengths = r.model.card_strengths()
    lines += ["", "## Strongest and weakest cards at equal skill (logistic regression)", "",
              "| Strongest | Weight | Weakest | Weight |", "| --- | --- | --- | --- |"]
    for (sn, sw), (wn, ww) in zip(strengths[:10], reversed(strengths[-10:])):
        lines.append(f"| {sn} | {sw:+.3f} | {wn} | {ww:+.3f} |")
    lines += [
        "",
        f"- Skill weight: {r.model.skill_weight:+.3f}. All else equal, a player ranked 10x "
        f"higher (e.g. #100 vs #1,000) wins about {_pct(_ten_times_better(r.model.skill_weight))}.",
        f"- Level weight: {r.model.level_weight:+.3f} per average level of advantage.",
        "",
        "## Limits",
        "",
        "- Rank only exists for the top 10,000; below that, players count as equally skilled.",
        "- Card weights are average strength in the current meta, not counters; "
        "the tree model is the one that can learn card-vs-card interactions.",
        "- Rare cards are left out until they've been seen enough times.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train DeckLens matchup models.")
    parser.add_argument("--days", type=int, default=14,
                        help="only battles from the last N days (0 for all)")
    parser.add_argument("--min-league", type=int, help="only battles in this league or higher")
    parser.add_argument("--test-fraction", type=float, default=0.2,
                        help="newest share of battles held out for testing")
    parser.add_argument("--min-card-count", type=int, default=30,
                        help="ignore cards seen fewer times than this in training")
    parser.add_argument("--no-trees", action="store_true", help="skip the gradient-boosted model")
    parser.add_argument("--report-dir", default="reports")
    parser.add_argument("--model-dir", default="models")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.days < 0:
        parser.error("--days must be 0 or more")

    try:
        settings = load_settings()
    except ConfigError as exc:
        log.error("%s", exc)
        return 2

    import psycopg

    from .dataset import load_matchups

    since = datetime.now(timezone.utc) - timedelta(days=args.days) if args.days else None
    with psycopg.connect(settings.database_url) as conn:
        matchups, names = load_matchups(conn, min_league=args.min_league, since=since)
    window = f"last {args.days} days" if args.days else "all time"
    log.info("loaded %d decided ranked battles (%s)", len(matchups), window)

    try:
        result = run_training(
            matchups, names, test_fraction=args.test_fraction,
            min_card_count=args.min_card_count, min_league=args.min_league,
            days=args.days or None, with_trees=not args.no_trees,
        )
    except ValueError as exc:
        log.error("%s", exc)
        return 1

    stamp = f"{result.cutoff:%Y-%m-%d}"
    report_path = Path(args.report_dir) / f"model-{stamp}.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_report(result), encoding="utf-8")
    model_path = Path(args.model_dir) / f"decklens-linear-{stamp}.json"
    result.model.save(model_path)

    log.info("skill data: both ranked %s, one ranked %s",
             _pct(result.both_ranked), _pct(result.one_ranked))
    for e in result.evaluations:
        lo, hi = e.accuracy_ci
        log.info("%-52s accuracy %s (%s-%s)  log loss %.4f",
                 e.name, _pct(e.accuracy), _pct(lo), _pct(hi), e.log_loss)
    log.info("report: %s", report_path)
    log.info("model:  %s", model_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
