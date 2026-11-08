"""Known counters, kept by hand: the cards each win condition struggles against.

This is player knowledge, not something the models learned. Matchup models need
many games between two specific decks to see a counter, and rare decks (X-Bow)
don't get them, so the odds can sit near 50% for a matchup any X-Bow player
knows is lost. The scout page lists these counters next to the odds as a
warning; they never change the percentage, so the odds stay what the data says.

To add one: the win condition's name as the key, the cards that counter it as
the value. Names are the Clash Royale API's English names ("X-Bow", "The Log",
"P.E.K.K.A"); a misspelt name never matches. A test checks each key is one of
archetypes.WIN_CONDITIONS; check counter names against the card list yourself.
"""

from __future__ import annotations

from typing import Iterable

COUNTERS: dict[str, tuple[str, ...]] = {
    "X-Bow": ("Arrows", "Lightning", "Royal Recruits", "Golem"),
}


def counters_in(attacker: Iterable[str], defender: Iterable[str]) -> list[dict[str, object]]:
    """For each of the attacker's win conditions with known counters, the counters
    the defender's deck has: [{"win_condition": "X-Bow", "cards": ["Arrows", ...]}]."""
    theirs = set(defender)
    found = []
    for win_condition in sorted(set(attacker)):
        cards = [c for c in COUNTERS.get(win_condition, ()) if c in theirs]
        if cards:
            found.append({"win_condition": win_condition, "cards": cards})
    return found
