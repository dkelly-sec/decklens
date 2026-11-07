"""Reproduction for docs/audits/2026-11-07-web-features.md, finding F1.

THREAT_MODEL.md promised that one live clan lookup costs "at most 1 clan search + 5
member lists", but with a name that matched nobody one lookup fetched up to 40 member
lists (CLANS_TO_CHECK_WITH_NAME), each a separate call on the shared API key, and the
6-a-minute limit counted lookups, not calls: about 246 upstream calls a minute per client.

Fix (owner's reading, 2026-11-07): keep the 40-clan search, which finds opponents in
common clan names, and cap what one client can spend: at most 60 member lists a minute
(the "clan_members" limit, charged only when the API is really asked) on top of 6 clan
searches. THREAT_MODEL.md now documents those numbers.
"""

from test_web import ClanClient, _seen, make

LOOKUPS_PER_MINUTE = 6       # RATE_LIMITS["clan_lookup"]
MEMBER_LISTS_PER_MINUTE = 60  # RATE_LIMITS["clan_members"]
MINUTE_BUDGET = LOOKUPS_PER_MINUTE + MEMBER_LISTS_PER_MINUTE


class ManyNamesakes(ClanClient):
    """A common clan name shared by 60 clans, none holding the player we look for."""

    def search_clans(self, name, limit=10):
        """Every clan name is its own set of 60 clans (own tags), so nothing is cached."""
        self.calls.append(("clans", name))
        seen = self.__dict__.setdefault("names", [])
        if name not in seen:
            seen.append(name)
        suffix = "" if name == "YEET" else "0289PYLQGRJCUV"[seen.index(name) % 14] * 2
        return [{"tag": f"#{a}{b}{c}{suffix}", "name": name}
                for a in "2890PQ" for b in "PQRUVY" for c in "GJ"][:60]

    def get_clan_members(self, tag):
        self.calls.append(("members", tag))
        if tag == "#2PG":
            return [{"tag": "#V8Q9LV8GC", "name": "DaHveed", "lastSeen": _seen(1)}]
        return [{"tag": "#LLL", "name": "Matt", "lastSeen": _seen(30)}]


def upstream_calls(client):
    return [c for c in client.calls if c[0] in ("clans", "members")]


def test_a_minute_of_lookups_stays_within_the_documented_upstream_budget():
    client = ManyNamesakes()
    app, _, _ = make(client=client)
    for i in range(LOOKUPS_PER_MINUTE):   # a different common name each time: no cache hits
        response = app.get("/api/clan-lookup", params={"clan": f"YEET {i}", "name": "zzqxnobody"})
        assert response.status_code == 200
    calls = upstream_calls(client)
    assert len(calls) <= MINUTE_BUDGET, (
        f"one client made {len(calls)} upstream calls in a minute; the budget is {MINUTE_BUDGET}")


def test_a_lookup_still_finds_a_member():
    client = ManyNamesakes()
    app, _, _ = make(client=client)
    data = app.get("/api/clan-lookup", params={"clan": "YEET", "name": "DaHveed"}).json()
    assert data["players"] and data["players"][0]["name"] == "DaHveed"


def test_clans_skipped_for_budget_are_not_cached_or_reported_as_checked():
    client = ManyNamesakes()
    app, services, _ = make(client=client)
    first = app.get("/api/clan-lookup", params={"clan": "YEET", "name": "zzqxnobody"}).json()
    assert len(first["clans_checked"]) == 40 and not first["budget_reached"]
    second = app.get("/api/clan-lookup", params={"clan": "YOLO", "name": "zzqxnobody"}).json()
    assert second["budget_reached"]            # 40 + 20 = the minute's 60 member lists
    assert len(second["clans_checked"]) == 20  # skipped clans aren't reported as checked

    services.limiter = type(services.limiter)(dict(services.limiter.limits))  # a new minute
    client.calls.clear()
    third = app.get("/api/clan-lookup", params={"clan": "YOLO", "name": "zzqxnobody"}).json()
    assert len(third["clans_checked"]) == 40 and not third["budget_reached"]
    fetched = [c for c in client.calls if c[0] == "members"]
    assert len(fetched) == 20                  # the 20 skipped earlier weren't cached as empty
