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
