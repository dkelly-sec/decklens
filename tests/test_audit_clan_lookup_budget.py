"""Reproduction for docs/audits/2026-11-07-web-features.md, finding F1.

THREAT_MODEL.md promises that one live clan lookup costs "at most 1 clan search + 5
member lists". With a name that matches nobody, one lookup fetches up to 40 member
lists (CLANS_TO_CHECK_WITH_NAME), each a separate call on the shared API key.

test_one_lookup_stays_within_the_documented_upstream_budget fails while the defect is
present (41 upstream calls). test_a_lookup_still_finds_a_member is the legitimate path
and must keep passing whatever fix is chosen.

The attack tests are marked xfail(strict=True) so CI stays green while the defect is open;
a fix makes them pass, strict xfail then fails the build until the mark is removed.
Run them as plain tests with: python -m pytest --runxfail <this file>
"""

import pytest

from test_web import ClanClient, _seen, make

DOCUMENTED_BUDGET = 1 + 5  # THREAT_MODEL.md, "Live clan lookup" row


class ManyNamesakes(ClanClient):
    """A common clan name shared by 60 clans, none holding the player we look for."""

    def search_clans(self, name, limit=10):
        self.calls.append(("clans", name))
        return [{"tag": f"#{a}{b}{c}", "name": "YEET"}
                for a in "2890PQ" for b in "PQRUVY" for c in "GJ"][:60]

    def get_clan_members(self, tag):
        self.calls.append(("members", tag))
        if tag == "#2PG":
            return [{"tag": "#V8Q9LV8GC", "name": "DaHveed", "lastSeen": _seen(1)}]
        return [{"tag": "#LLL", "name": "Matt", "lastSeen": _seen(30)}]


def upstream_calls(client):
    return [c for c in client.calls if c[0] in ("clans", "members")]


@pytest.mark.xfail(strict=True, reason="2026-11-07-web-features F1: open defect; remove this mark in the fix")
def test_one_lookup_stays_within_the_documented_upstream_budget():
    client = ManyNamesakes()
    app, _, _ = make(client=client)
    response = app.get("/api/clan-lookup", params={"clan": "YEET", "name": "zzqxnobody"})
    assert response.status_code == 200
    calls = upstream_calls(client)
    assert len(calls) <= DOCUMENTED_BUDGET, (
        f"one lookup made {len(calls)} upstream calls; THREAT_MODEL.md allows {DOCUMENTED_BUDGET}")


def test_a_lookup_still_finds_a_member():
    client = ManyNamesakes()
    app, _, _ = make(client=client)
    data = app.get("/api/clan-lookup", params={"clan": "YEET", "name": "DaHveed"}).json()
    assert data["players"] and data["players"][0]["name"] == "DaHveed"
