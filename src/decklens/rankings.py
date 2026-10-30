"""Parse Ranked leaderboard entries and player profiles into rank snapshots.

The API's exact field names for rating vary by endpoint, so each parser
checks the known spellings and keeps whatever is a plain integer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .battles import opt_int
from .validation import InvalidTagError, normalize_tag


@dataclass(frozen=True)
class RankEntry:
    tag: str
    name: str
    rating: int | None
    rank: int | None
    clan_tag: str | None
    clan_name: str | None


def _first_int(raw: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = opt_int(raw.get(key))
        if value is not None:
            return value
    return None


def parse_leaderboard(items: Iterable[dict[str, Any]]) -> list[RankEntry]:
    """Turn leaderboard items into RankEntry objects, skipping malformed ones."""
    entries: list[RankEntry] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            tag = normalize_tag(item.get("tag"))
        except InvalidTagError:
            continue
        if tag in seen:
            continue
        seen.add(tag)
        clan = item.get("clan") if isinstance(item.get("clan"), dict) else {}
        entries.append(
            RankEntry(
                tag=tag,
                name=str(item.get("name", ""))[:64],
                rating=_first_int(item, "eloRating", "rating", "trophies"),
                rank=_first_int(item, "rank"),
                clan_tag=clan.get("tag"),
                clan_name=clan.get("name"),
            )
        )
    return entries


def parse_profile_rank(profile: dict[str, Any]) -> RankEntry | None:
    """Current-season Ranked result from a player profile, if present."""
    if not isinstance(profile, dict):
        return None
    try:
        tag = normalize_tag(profile.get("tag"))
    except InvalidTagError:
        return None
    result = profile.get("currentPathOfLegendSeasonResult")
    result = result if isinstance(result, dict) else {}
    clan = profile.get("clan") if isinstance(profile.get("clan"), dict) else {}
    return RankEntry(
        tag=tag,
        name=str(profile.get("name", ""))[:64],
        rating=_first_int(result, "trophies", "rating"),
        rank=_first_int(result, "rank"),
        clan_tag=clan.get("tag"),
        clan_name=clan.get("name"),
    )
