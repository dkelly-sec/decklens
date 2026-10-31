import pytest
import requests

from decklens.api_client import BASE_URL, ApiError, ClashRoyaleClient, NotFoundError
from decklens.validation import InvalidTagError

TOKEN = "test-token-not-real"


class FakeResponse:
    def __init__(self, status, body=None, headers=None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}

    def json(self):
        return self._body


class FakeSession:
    """Returns queued responses and records every call made."""

    def __init__(self, *responses):
        self.headers = {}
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def make(*responses):
    session = FakeSession(*responses)
    sleeps = []
    client = ClashRoyaleClient(TOKEN, session=session, sleep=sleeps.append, max_retries=3)
    return client, session, sleeps


def test_battle_log_url_is_fixed_and_tag_encoded():
    client, session, _ = make(FakeResponse(200, []))
    client.get_battle_log("#2PP")
    url, kwargs = session.calls[0]
    assert url == f"{BASE_URL}/players/%232PP/battlelog"
    assert kwargs["timeout"] > 0
    assert kwargs["allow_redirects"] is False


def test_token_sent_in_header_only():
    client, session, _ = make(FakeResponse(200, {}))
    client.get_player("#2PP")
    assert session.headers["Authorization"] == f"Bearer {TOKEN}"
    assert TOKEN not in session.calls[0][0]


def test_token_not_in_repr():
    client, _, _ = make()
    assert TOKEN not in repr(client)


def test_invalid_tag_never_reaches_the_network():
    client, session, _ = make()
    with pytest.raises(InvalidTagError):
        client.get_battle_log("#2PP/../../clans")
    assert session.calls == []


def test_retries_on_429_honouring_retry_after():
    client, session, sleeps = make(
        FakeResponse(429, headers={"Retry-After": "3"}),
        FakeResponse(503),
        FakeResponse(200, []),
    )
    assert client.get_battle_log("#2PP") == []
    assert len(session.calls) == 3
    assert sleeps[0] == 3.0


def test_retry_after_is_capped():
    client, _, sleeps = make(
        FakeResponse(429, headers={"Retry-After": "99999"}),
        FakeResponse(200, []),
    )
    client.get_battle_log("#2PP")
    assert sleeps[0] == 60.0


def test_gives_up_after_max_retries():
    client, session, _ = make(*[FakeResponse(503) for _ in range(4)])
    with pytest.raises(ApiError) as err:
        client.get_battle_log("#2PP")
    assert err.value.status == 503
    assert len(session.calls) == 4


def test_network_errors_are_retried_then_wrapped():
    client, _, _ = make(*[requests.ConnectionError("boom") for _ in range(4)])
    with pytest.raises(ApiError, match="ConnectionError"):
        client.get_battle_log("#2PP")


def test_404_raises_not_found():
    client, _, _ = make(FakeResponse(404))
    with pytest.raises(NotFoundError):
        client.get_player("#2PP")


def test_403_explains_ip_whitelist_without_leaking_token():
    client, _, _ = make(FakeResponse(403))
    with pytest.raises(ApiError) as err:
        client.get_player("#2PP")
    assert "whitelist" in str(err.value)
    assert TOKEN not in str(err.value)


def test_redirect_is_not_followed():
    client, _, _ = make(FakeResponse(302, headers={"Location": "https://evil.example"}))
    with pytest.raises(ApiError):
        client.get_player("#2PP")


# --- Ranked leaderboard paging -------------------------------------------------


def page(tags, after=None):
    body = {"items": [{"tag": t, "rank": i} for i, t in enumerate(tags, 1)]}
    if after:
        body["paging"] = {"cursors": {"after": after}}
    return FakeResponse(200, body)


def test_leaderboard_follows_cursor_paging():
    client, session, _ = make(page(["#2PP", "#9QJ"], after="eyJwb3MiOjJ9"), page(["#8YY"]))
    items = client.get_ranked_leaderboard("global", max_items=10)
    assert [i["tag"] for i in items] == ["#2PP", "#9QJ", "#8YY"]
    first_url, first_kwargs = session.calls[0]
    assert first_url == f"{BASE_URL}/locations/global/pathoflegend/players"
    assert first_kwargs["params"] == {"limit": 10}
    assert session.calls[1][1]["params"] == {"limit": 8, "after": "eyJwb3MiOjJ9"}


def test_leaderboard_stops_at_max_items():
    client, session, _ = make(page(["#2PP", "#9QJ", "#8YY"], after="abc"))
    items = client.get_ranked_leaderboard("global", max_items=2)
    assert len(items) == 2
    assert len(session.calls) == 1


def test_untrusted_cursor_stops_paging():
    client, session, _ = make(page(["#2PP"], after="../../players?x=1"))
    client.get_ranked_leaderboard("global", max_items=10)
    assert len(session.calls) == 1


def test_empty_leaderboard_returns_nothing():
    client, _, _ = make(FakeResponse(200, {"items": []}))
    assert client.get_ranked_leaderboard("global") == []


@pytest.mark.parametrize("bad", ["../players", "global/../x", "57000006?x", "", "abc"])
def test_invalid_location_never_reaches_the_network(bad):
    client, session, _ = make()
    with pytest.raises(ValueError):
        client.get_ranked_leaderboard(bad)
    assert session.calls == []


def test_numeric_location_is_allowed():
    client, session, _ = make(FakeResponse(200, {"items": []}))
    client.get_ranked_leaderboard("57000006")
    assert session.calls[0][0].endswith("/locations/57000006/pathoflegend/players")


def test_season_leaderboard_path_and_validation():
    client, session, _ = make(FakeResponse(200, {"items": []}))
    client.get_ranked_season_leaderboard("2026-09")
    assert session.calls[0][0] == f"{BASE_URL}/locations/global/pathoflegend/2026-09/rankings/players"
    with pytest.raises(ValueError):
        client.get_ranked_season_leaderboard("2026-09/../../players")


def test_clan_search_puts_the_name_only_in_the_query_string():
    client, session, _ = make(FakeResponse(200, {"items": [{"tag": "#9QJ", "name": "x"}, "junk"]}))
    clans = client.search_clans("  ../../players/#2PP?x=1 ", limit=999)
    url, kwargs = session.calls[0]
    assert url == BASE_URL + "/clans"                       # path never changes
    assert kwargs["params"] == {"name": "../../players/#2PP?x=1", "limit": 50}
    assert clans == [{"tag": "#9QJ", "name": "x"}]


@pytest.mark.parametrize("bad", ["", "ab", "  ab  ", "x" * 51, "bad\nname", None])
def test_clan_search_validates_before_any_request(bad):
    client, session, _ = make()
    with pytest.raises(ValueError):
        client.search_clans(bad)
    assert session.calls == []


def test_clan_members_path_is_fixed_and_tag_encoded():
    client, session, _ = make(FakeResponse(200, {"items": [{"tag": "#2PP", "lastSeen": "x"}]}))
    assert client.get_clan_members("#9qj") == [{"tag": "#2PP", "lastSeen": "x"}]
    assert session.calls[0][0] == BASE_URL + "/clans/%239QJ/members"
    with pytest.raises(InvalidTagError):
        client.get_clan_members("../players")
