import re
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from decklens.api_client import ApiError, NotFoundError
from decklens.archetypes import ArchetypeModel
from decklens.features import CardVocab
from decklens.model import LinearMatchupModel, time_split
from decklens.web.app import STATIC_DIR, Services, create_app
from decklens.web.security import RateLimiter
from decklens.web.services import Predictor

from synthetic import card_id, core_cards, make_archetype_matchups

ME, OPP = "#2PP", "#9QJ"
HOSTILE = "<script>alert(1)</script>" + "x" * 100


def cards(ids):
    return [{"id": int(c), "name": f"Card {int(c) - 26000000}", "level": 16, "maxLevel": 16}
            for c in ids]


MY_DECK = core_cards(0) + (card_id(40), card_id(41), card_id(42))
OPP_DECK = core_cards(1) + (card_id(43), card_id(44), card_id(45))


def battle(minute, me_won=True, kind="pathOfLegend", opp=OPP, opp_name=HOSTILE):
    return {
        "type": kind, "battleTime": f"20261030T01{minute:02d}00.000Z", "leagueNumber": 7,
        "team": [{"tag": ME, "name": "Me", "crowns": 1 if me_won else 0, "cards": cards(MY_DECK)}],
        "opponent": [{"tag": opp, "name": opp_name, "crowns": 0 if me_won else 1,
                      "globalRank": 812, "cards": cards(OPP_DECK)}],
    }


class FakeClient:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def get_player(self, tag):
        self.calls.append(("player", tag))
        if self.error:
            raise self.error
        return {"tag": tag, "name": "Me", "clan": {"name": "Clan"},
                "currentPathOfLegendSeasonResult": {"leagueNumber": 7, "trophies": 2900, "rank": 150},
                "currentDeck": cards(MY_DECK)}

    def get_battle_log(self, tag):
        self.calls.append(("battlelog", tag))
        if self.error:
            raise self.error
        if tag == OPP:
            mine = battle(5)
            mine["team"], mine["opponent"] = mine["opponent"], mine["team"]
            return [mine, mine | {"battleTime": "20261030T010000.000Z"}]
        return [battle(9, kind="trail"), battle(8, me_won=False), battle(3)]


class FakeConn:
    def __init__(self, rows):
        self.rows, self.executed = rows, []

    @contextmanager
    def cursor(self):
        yield self

    def execute(self, sql, params):
        self.executed.append(params)

    def fetchall(self):
        return self.rows


@pytest.fixture(scope="module")
def predictor():
    matchups, names, _ = make_archetype_matchups(4000, seed=2)
    train, _, _ = time_split(matchups, 0.2)
    vocab = CardVocab.build(train, names=names)
    return Predictor(ArchetypeModel.fit(vocab, train, 6, 20.0), LinearMatchupModel.fit(vocab, train))


def make(predictor=None, client=None, rows=None, limits=None, search=True):
    conn = FakeConn(rows or [])

    @contextmanager
    def search_db():
        yield conn

    services = Services(client=client or FakeClient(), predictor=predictor,
                        search_db=search_db if search else None)
    if limits:
        services.limiter = RateLimiter(limits)
    return TestClient(create_app(services), raise_server_exceptions=False), services, conn


# --- security headers and surface --------------------------------------------------


def test_security_headers_on_every_response():
    app, _, _ = make()
    for path in ("/", "/api/health", "/static/app.js", "/does-not-exist"):
        headers = app.get(path).headers
        csp = headers["content-security-policy"]
        assert "script-src 'self'" in csp and "unsafe-inline" not in csp
        assert "frame-ancestors 'none'" in csp and "default-src 'none'" in csp
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["referrer-policy"] == "no-referrer"
        assert "strict-transport-security" not in headers  # only when served over HTTPS
    assert app.get("/api/health").headers["cache-control"] == "no-store"


def test_hsts_when_https():
    app = TestClient(create_app(Services(client=FakeClient()), hsts=True))
    assert app.get("/").headers["strict-transport-security"] == "max-age=31536000"


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_no_interactive_docs_or_schema(path):
    app, _, _ = make()
    assert app.get(path).status_code == 404


def test_page_and_static_files_are_served():
    app, _, _ = make()
    page = app.get("/")
    assert page.status_code == 200 and "DeckLens" in page.text
    assert app.get("/static/app.js").status_code == 200
    assert app.get("/static/../app.py").status_code == 404  # no escaping the static folder


# --- player lookups ------------------------------------------------------------------


def test_player_lookup_and_cache():
    app, services, _ = make()
    first = app.get("/api/players/%232PP").json()
    assert first["name"] == "Me" and first["rank"] == 150 and len(first["deck"]) == 8
    app.get("/api/players/2pp")  # same player, written differently
    assert services.client.calls == [("player", "#2PP")]  # second answer came from the cache


@pytest.mark.parametrize("bad", ["%3Cscript%3E", "%232PP%2F..%2Fclans", "ABC", "%23" + "2" * 20])
def test_invalid_tags_never_reach_the_api(bad):
    app, services, _ = make()
    response = app.get(f"/api/players/{bad}")
    assert response.status_code in (400, 404)
    assert services.client.calls == []


@pytest.mark.parametrize("error, status, message", [
    (NotFoundError("gone", 404), 404, "No player with that tag"),
    (ApiError("forbidden", 403), 503, "Lookups are unavailable right now"),
    (ApiError("HTTP 500", 500), 502, "The Clash Royale API isn't responding"),
])
def test_upstream_errors_map_to_safe_messages(error, status, message):
    app, _, _ = make(client=FakeClient(error))
    response = app.get("/api/players/%232PP")
    assert response.status_code == status and response.json() == {"detail": message}


def test_unexpected_errors_reveal_nothing():
    app, _, _ = make(client=FakeClient(RuntimeError("db password is hunter2")))
    response = app.get("/api/players/%232PP")
    assert response.status_code == 500
    assert response.json() == {"detail": "Something went wrong"}
    assert "hunter2" not in response.text


def test_rate_limit_returns_429_with_retry_after():
    app, _, _ = make(limits={"lookup": (2, 60.0), "search": (2, 60.0), "predict": (2, 60.0)})
    codes = [app.get("/api/players/%232PP").status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    limited = app.get("/api/players/%232PP")
    assert int(limited.headers["retry-after"]) >= 1


# --- scouting ------------------------------------------------------------------------


def test_last_opponent_with_matchup(predictor):
    app, _, _ = make(predictor)
    data = app.get("/api/players/%232PP/last-opponent").json()
    assert data["tag"] == OPP and data["your_result"] == "lost"  # newest ranked battle; trail skipped
    assert data["rank"] == 812 and len(data["deck"]) == 8
    assert data["name"] == HOSTILE[:64]  # hostile name: length-limited, returned as plain data
    assert 0 <= data["matchup"]["win_chance"] <= 1
    assert data["matchup"]["archetype_a"] != data["matchup"]["archetype_b"]


def test_opponent_recent_decks():
    app, _, _ = make()
    data = app.get("/api/players/%239QJ/decks").json()
    assert data["decks"][0]["games"] == 2 and data["decks"][0]["wins"] == 0


def test_last_opponent_without_ranked_battles():
    client = FakeClient()
    client.get_battle_log = lambda tag: [battle(1, kind="trail")]
    app, _, _ = make(client=client)
    assert app.get("/api/players/%232PP/last-opponent").status_code == 404


# --- search --------------------------------------------------------------------------


def test_search_normalises_and_escapes(predictor):
    rows = [("#9QJ", HOSTILE, "Clan", 812)]
    app, _, conn = make(rows=rows)
    response = app.get("/api/search", params={"name": "🔥Dípo%_", "near_rank": 150})
    assert response.status_code == 200
    assert response.json()["results"][0]["tag"] == "#9QJ"
    pattern, near, _, limit = conn.executed[0]
    assert pattern == "dipo%" and near == 150 and limit == 5  # no user wildcards survive


@pytest.mark.parametrize("params, status", [
    ({"name": "🔥"}, 400),               # nothing searchable left after normalising
    ({"name": "x" * 41}, 422),           # too long
    ({"name": "dipo", "near_rank": 0}, 422),
    ({}, 422),
])
def test_search_input_validation(params, status):
    app, _, _ = make()
    assert app.get("/api/search", params=params).status_code == status


def test_search_unavailable_without_database():
    app, _, _ = make(search=False)
    assert app.get("/api/search", params={"name": "dipo"}).status_code == 503


# --- prediction ----------------------------------------------------------------------


def test_predict(predictor):
    app, _, _ = make(predictor)
    response = app.post("/api/predict", json={"deck_a": list(MY_DECK), "deck_b": list(OPP_DECK)})
    data = response.json()
    assert response.status_code == 200 and 0 <= data["win_chance"] <= 1
    assert data["note"] and data["key_cards"]


@pytest.mark.parametrize("body, status", [
    ({"deck_a": list(MY_DECK)[:7], "deck_b": list(OPP_DECK)}, 400),        # 7 cards
    ({"deck_a": [card_id(10)] * 8, "deck_b": list(OPP_DECK)}, 400),         # duplicates
    ({"deck_a": list(range(1, 9)), "deck_b": list(OPP_DECK)}, 400),         # unknown cards
    ({"deck_a": ["x"] * 8, "deck_b": list(OPP_DECK)}, 422),                 # not numbers
    ({"deck_a": list(MY_DECK), "deck_b": list(OPP_DECK), "admin": True}, 422),  # extra field
])
def test_predict_input_validation(predictor, body, status):
    app, _, _ = make(predictor)
    assert app.post("/api/predict", json=body).status_code == status


def test_oversized_body_is_rejected(predictor):
    app, _, _ = make(predictor)
    response = app.post("/api/predict", content=b"{" + b" " * 10_000 + b"}",
                        headers={"content-type": "application/json"})
    assert response.status_code == 413


def test_predict_without_model():
    app, _, _ = make(predictor=None)
    response = app.post("/api/predict", json={"deck_a": list(MY_DECK), "deck_b": list(OPP_DECK)})
    assert response.status_code == 503


# --- front-end safety ----------------------------------------------------------------


def test_front_end_never_builds_html_from_data():
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    code = "\n".join(line for line in js.splitlines() if not line.strip().startswith("//"))
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(",
                   "new Function", "setTimeout(\"", "setInterval(\""):
        assert banned not in code, banned


def test_page_has_no_inline_script_style_or_handlers():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert re.findall(r"<script(?![^>]*\bsrc=)", html) == []  # every script is a file
    assert "<style" not in html and " style=" not in html
    assert re.search(r"\son[a-z]+=", html) is None           # no onclick= etc.


def test_search_database_down_gives_a_clear_503():
    import psycopg

    @contextmanager
    def broken():
        raise psycopg.OperationalError("connection refused, password=hunter2")
        yield  # pragma: no cover

    app = TestClient(create_app(Services(client=FakeClient(), search_db=broken)),
                     raise_server_exceptions=False)
    response = app.get("/api/search", params={"name": "dipo"})
    assert response.status_code == 503 and "hunter2" not in response.text


def test_placeholder_database_url_turns_search_off(monkeypatch, tmp_path):
    from decklens.web.app import create_app_from_env

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CR_API_TOKEN", "test-token")
    monkeypatch.setenv("WEB_DATABASE_URL", "postgresql://decklens_web:replace-me@127.0.0.1/x")
    monkeypatch.setenv("DECKLENS_MODEL_DIR", str(tmp_path))
    health = TestClient(create_app_from_env()).get("/api/health").json()
    assert health == {"status": "ok", "model": False, "search": False}
