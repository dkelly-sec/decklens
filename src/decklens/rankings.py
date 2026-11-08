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


# Ranked's top league. Only there do players have a rating (medals) and a
# leaderboard rank. (Old Path of Legend data numbered it 10, hence >=.)
ULTIMATE_CHAMPION_LEAGUE = 7


def is_ultimate_champion(league: int | None) -> bool:
    return league is not None and league >= ULTIMATE_CHAMPION_LEAGUE


def current_season_result(profile: dict[str, Any]) -> dict[str, Any]:
    """The profile's current-season Ranked result, without last season's numbers.

    Early in a season the API keeps showing last season's final rank and rating
    in currentPathOfLegendSeasonResult. Below Ultimate Champion there is no rank
    or rating this season, so any there are last season's; and in Ultimate
    Champion, numbers matching lastPathOfLegendSeasonResult are last season's
    too. Either way, drop the rank and rating (the league stays)."""
    current = profile.get("currentPathOfLegendSeasonResult")
    current = dict(current) if isinstance(current, dict) else {}
    last = profile.get("lastPathOfLegendSeasonResult")
    last = last if isinstance(last, dict) else {}
    league = _first_int(current, "leagueNumber")
    rank, last_rank = _first_int(current, "rank"), _first_int(last, "rank")
    below_top = league is not None and not is_ultimate_champion(league)
    repeated = rank is not None and rank == last_rank and (
        _first_int(current, "trophies", "rating") == _first_int(last, "trophies", "rating"))
    if below_top or repeated:
        current.pop("rank", None)
        current.pop("trophies", None)
        current.pop("rating", None)
    return current


def parse_profile_rank(profile: dict[str, Any]) -> RankEntry | None:
    """Current-season Ranked result from a player profile, if present."""
    if not isinstance(profile, dict):
        return None
    try:
        tag = normalize_tag(profile.get("tag"))
    except InvalidTagError:
        return None
    result = current_season_result(profile)
    clan = profile.get("clan") if isinstance(profile.get("clan"), dict) else {}
    return RankEntry(
        tag=tag,
        name=str(profile.get("name", ""))[:64],
        rating=_first_int(result, "trophies", "rating"),
        rank=_first_int(result, "rank"),
        clan_tag=clan.get("tag"),
        clan_name=clan.get("name"),
    )
