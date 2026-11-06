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


def cards(ids, level=16):
    return [{"id": int(c), "name": f"Card {int(c) - 26000000}", "level": level, "maxLevel": 16,
             "elixirCost": 2 + i % 4, "rarity": "Common"}
            for i, c in enumerate(ids)]


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


ORIGIN = {"origin": "http://testserver"}  # what a browser sends from our own page


def make(predictor=None, client=None, rows=None, limits=None, search=True, auth=None):
    conn = FakeConn(rows or [])

    @contextmanager
    def search_db():
        yield conn

    services = Services(client=client or FakeClient(), predictor=predictor,
                        search_db=search_db if search else None, auth=auth)
    if limits:
        services.limiter = RateLimiter(limits)
    return (TestClient(create_app(services), raise_server_exceptions=False, headers=ORIGIN),
            services, conn)


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


def test_page_and_static_files_are_revalidated_so_updates_show_up():
    # Without this, a browser may reuse yesterday's style.css/app.js after an update.
    app, _, _ = make()
    for path in ("/", "/static/app.js", "/static/style.css"):
        assert app.get(path).headers["cache-control"] == "no-cache", path
    first = app.get("/static/style.css")
    again = app.get("/static/style.css", headers={"if-none-match": first.headers["etag"]})
    assert again.status_code == 304  # unchanged files cost almost nothing to check
    # Routes that set their own caching keep it.
    assert app.get("/favicon.ico").headers["cache-control"] == "public, max-age=86400"


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
    assert first["name"] == "Me" and first["rank"] == 150 and len(first["deck"]["cards"]) == 8
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
    assert data["rank"] == 812 and len(data["deck"]["cards"]) == 8
    assert data["name"] == HOSTILE[:64]  # hostile name: length-limited, returned as plain data
    assert 0 <= data["matchup"]["win_chance"] <= 1
    assert data["matchup"]["archetype_a"] != data["matchup"]["archetype_b"]


def test_opponent_recent_decks():
    app, _, _ = make()
    data = app.get("/api/players/%239QJ/decks").json()
    ranked = data["modes"]["ranked"]
    assert ranked[0]["games"] == 2 and ranked[0]["wins"] == 0
    assert data["modes"]["challenge"] == [] and data["modes"]["trophy"] == []
    assert data["recent_modes"] == {"Ranked": 2}


def test_last_opponent_without_ranked_battles():
    client = FakeClient()
    client.get_battle_log = lambda tag: [battle(1, kind="trail")]
    app, _, _ = make(client=client)
    assert app.get("/api/players/%232PP/last-opponent").status_code == 404


# --- search --------------------------------------------------------------------------


def test_search_normalises_and_escapes(predictor):
    rows = [("#9QJ", HOSTILE, "Clan", 812, 2400, False, True)]
    app, _, conn = make(rows=rows)
    response = app.get("/api/search", params={"name": "🔥Dípo%_", "near_rating": 2136})
    assert response.status_code == 200
    assert response.json()["results"][0]["tag"] == "#9QJ"
    assert conn.executed[0] == ("0.45",)                                  # fuzzy threshold
    params = conn.executed[1]
    assert params == {"pattern": "dipo%", "key": "dipo", "clan_pattern": None, "clan_key": None,
                      "near": 2136, "limit": 10, "clan_match": 0.7,
                      "no_clan": False, "no_clan_bonus": 0.5,
                      "name_match": 0.45}  # no user wildcards survive
    assert response.json()["results"][0]["rating"] == 2400


def test_search_by_clan_when_the_name_is_all_emoji():
    app, _, conn = make(rows=[("#9QJ", "🔥🔥🔥", "Persian Warrior", None, 2079, True, False)])
    response = app.get("/api/search", params={"name": "🔥🔥", "clan": "Persian War%_"})
    assert response.status_code == 200 and response.json()["results"][0]["name"] == "🔥🔥🔥"
    assert response.json()["results"][0]["clan_match"] is True
    params = conn.executed[1]
    assert params["pattern"] is None and params["key"] is None           # emoji name ignored
    assert params["clan_pattern"] == "persianwar%" and params["clan_key"] == "persianwar"

    app.get("/api/search", params={"name": "aj", "clan": "persian"})     # both narrow it down
    assert conn.executed[3]["pattern"] == "aj%" and conn.executed[3]["clan_pattern"] == "persian%"


def test_search_no_clan_clue_only_applies_to_a_name_search():
    app, _, conn = make(rows=[("#9QJ", "Dipo", None, None, 2079, True, True)])
    response = app.get("/api/search", params={"name": "dipo", "no_clan": "1"})
    assert response.status_code == 200
    assert response.json()["results"][0] == {"tag": "#9QJ", "name": "Dipo", "clan": None,
                                             "rank": None, "rating": 2079, "clan_match": True,
                                             "name_match": True}
    assert conn.executed[1]["no_clan"] is True
    app.get("/api/search", params={"name": "dipo", "clan": "persian", "no_clan": "1"})
    assert conn.executed[3]["no_clan"] is False          # a typed clan wins
    assert app.get("/api/search", params={"name": "dipo", "no_clan": "maybe"}).status_code == 422


@pytest.mark.parametrize("params, status", [
    ({"name": "🔥"}, 400),               # nothing searchable left after normalising
    ({"name": "x" * 41}, 422),           # too long
    ({"name": "dipo", "near_rating": -1}, 422),
    ({"name": "dipo", "near_rating": 99999}, 422),
    ({}, 400),                           # neither name nor clan
    ({"name": "🔥", "clan": "⚔️"}, 400),  # nothing searchable in either
    ({"clan": "x" * 41}, 422),
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
    ({"deck_a": list(MY_DECK)[:6], "deck_b": list(OPP_DECK)}, 400),        # 6 cards
    ({"deck_a": list(MY_DECK) + [card_id(46)], "deck_b": list(OPP_DECK)}, 400),  # 9 cards
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
    monkeypatch.setenv("DECKLENS_LOGIN", "off")
    health = TestClient(create_app_from_env()).get("/api/health").json()
    assert health == {"status": "ok", "model": False, "search": False}



# --- deck details --------------------------------------------------------------------


def test_deck_details_and_summary():
    from decklens.web.services import _cards, deck_summary

    raw = [  # the game's slot order: Evolution slot, Hero slot, Wild slot, then plain cards
        {"id": 9, "name": "Skeletons", "elixirCost": 1, "level": 16, "maxLevel": 16,
         "rarity": "Common", "evolutionLevel": 1},
        {"id": 1, "name": "Hog Rider", "elixirCost": 4, "level": 14, "maxLevel": 16,
         "rarity": "Rare", "evolutionLevel": 0},
        {"id": 3, "name": "Mirror", "level": 16, "maxLevel": 16, "rarity": "<b>Epic</b>"},
        {"id": 4, "name": "Cannon", "elixirCost": 3, "level": "16", "maxLevel": 16},
        {"id": 5, "name": "Log", "elixirCost": 2, "level": 16, "maxLevel": 16},
        {"id": 6, "name": "Ice Spirit", "elixirCost": 1, "level": 16, "maxLevel": 16},
        {"id": 7, "name": "Musketeer", "elixirCost": 4, "level": 16, "maxLevel": 16},
        {"id": 8, "name": "Fireball", "elixirCost": 99, "level": 16, "maxLevel": 16},
    ]
    out = _cards(raw)
    assert [c["id"] for c in out] == [9, 1, 3, 4, 5, 6, 7, 8]  # slot order kept, not sorted
    cards_out = {c["id"]: c for c in out}
    assert cards_out[1]["below_max"] == 2 and cards_out[1]["rarity"] == "rare"
    assert cards_out[9]["form"] == "evo"
    assert cards_out[3]["elixir"] is None and cards_out[3]["rarity"] is None  # unknown values dropped
    assert cards_out[4]["below_max"] is None                                  # "16" isn't a number
    assert cards_out[8]["elixir"] is None                                     # impossible cost dropped
    summary = deck_summary(out)
    assert summary == {"avg_elixir": 2.5, "cycle": 7, "evolutions": 1, "heroes": 0, "either": 0,
                       "maxed": 6, "levels_known": 7, "hidden": 0}


@pytest.mark.parametrize("owned, slot, form", [
    (1, 0, "evo"), (2, 0, None), (3, 0, "evo"),        # Evolution slot: only evolutions play
    (1, 1, None), (2, 1, "hero"), (3, 1, "hero"),      # Hero slot: only heroes play
    (1, 2, "evo"), (2, 2, "hero"), (3, 2, "either"),   # Wild slot: both unlocked is ambiguous
    (3, 3, None), (1, 7, None),                        # ordinary slots play the plain card
    (0, 0, None), (None, 0, None), (4, 0, None), (-1, 2, None),  # junk ignored
])
def test_evolutions_and_heroes_depend_on_the_slot(owned, slot, form):
    from decklens.web.services import active_form

    assert active_form(owned, slot) == form


def test_hero_left_out_by_the_api_gets_a_placeholder_in_the_hero_slot():
    from decklens.web.services import _cards, card_ids, deck_summary

    raw = [{"id": 100 + i, "name": f"C{i}", "elixirCost": 3, "level": 16, "maxLevel": 16,
            "evolutionLevel": 3} for i in range(7)]
    out = _cards(raw)
    assert len(out) == 8 and out[1]["hidden"] is True and out[1]["id"] is None
    assert out[0]["form"] == "evo" and out[2]["form"] == "either" and out[3]["form"] is None
    assert card_ids(out) == [100 + i for i in range(7)] + [1_000_000_100]  # + Evo token for slot 1
    s = deck_summary(out)
    assert s["hidden"] == 1 and s["heroes"] == 1 and s["levels_known"] == 7 and s["avg_elixir"] == 3


def test_scouting_keeps_battles_where_a_hero_was_hidden(predictor):
    hidden = cards(OPP_DECK)[:7]
    client = FakeClient()
    entry = battle(5)
    entry["opponent"][0]["cards"] = hidden
    client.get_battle_log = lambda tag: [entry]
    app, _, _ = make(predictor, client=client)
    data = app.get("/api/players/%232PP/last-opponent").json()
    assert data["deck"]["summary"]["hidden"] == 1 and len(data["deck"]["cards"]) == 8
    assert "seven cards" in data["matchup"]["note"]

    from decklens.battles import parse_battle_log
    kept, skipped = parse_battle_log([entry])       # stored battles still need all 8 cards
    assert kept == [] and skipped == {"pathOfLegend (7-card deck, hidden Hero)": 1}


def test_no_ranked_decks_explains_what_they_played_instead():
    client = FakeClient()
    client.get_battle_log = lambda tag: [battle(1, kind="PvP"), battle(2, kind="PvP"),
                                         battle(3, kind="trail"), {"type": "<b>new</b>"}]
    app, _, _ = make(client=client)
    data = app.get("/api/players/%239QJ/decks").json()
    assert data["modes"]["ranked"] == [] and data["modes"]["challenge"] == []
    assert [d["games"] for d in data["modes"]["trophy"]] == [2]       # "PvP" is Trophy Road
    assert data["recent_modes"] == {"Trophy Road": 2, "Other challenges": 1, "Other modes": 1}


CHALLENGE = {"id": 72000474, "name": "Challenge_AllCards_EventDeck_NoSet"}


def test_challenge_and_trophy_road_decks_get_their_own_tabs():
    def challenge(minute, me_won=True):
        return battle(minute, me_won=me_won, kind="trail") | {"gameMode": CHALLENGE}

    trophy_road = battle(7, kind="trail") | {"gameMode": {"id": 72000006, "name": "Ladder"}}
    log = [trophy_road, challenge(6), challenge(5, me_won=False), challenge(4), battle(3)]
    client = FakeClient()
    client.get_battle_log = lambda tag: log
    app, _, _ = make(client=client)

    data = app.get("/api/players/%232PP/decks").json()
    assert [d["games"] for d in data["modes"]["challenge"]] == [3]
    challenge_deck = data["modes"]["challenge"][0]["deck"]
    assert challenge_deck["summary"]["fixed_levels"] is True               # event sets levels
    assert challenge_deck["summary"]["maxed"] is None
    assert all(c["below_max"] is None for c in challenge_deck["cards"])
    assert data["modes"]["ranked"][0]["deck"]["summary"]["maxed"] == 8     # ranked keeps levels
    assert data["modes"]["challenge"][0]["wins"] == 2
    assert [d["games"] for d in data["modes"]["ranked"]] == [1]
    trophy = data["modes"]["trophy"]
    assert [d["games"] for d in trophy] == [1] and trophy[0]["wins"] == 1
    assert trophy[0]["deck"]["summary"]["maxed"] == 8                      # real card levels
    assert "fixed_levels" not in trophy[0]["deck"]["summary"]
    assert data["recent_modes"] == {"20-win challenge": 3, "Trophy Road": 1, "Ranked": 1}

    last = app.get("/api/players/%232PP/last-opponent").json()
    assert last["mode"] == "trophy" and last["your_result"] == "won"     # newest: Trophy Road


def test_friendlies_and_other_events_stay_out_of_the_tabs():
    friendly = battle(4, kind="trail") | {"gameMode": {"name": "Friendly"}}
    other_event = battle(3, kind="trail") | {"gameMode": {"name": "Challenge_Touchdown"}}
    ladder_event = battle(2, kind="PvP") | {"gameMode": {"name": "Ladder_GoldRush"}}
    client = FakeClient()
    client.get_battle_log = lambda tag: [friendly, other_event, ladder_event]
    app, _, _ = make(client=client)
    modes = app.get("/api/players/%232PP/decks").json()["modes"]
    assert modes["challenge"] == [] and modes["ranked"] == []
    assert [d["games"] for d in modes["trophy"]] == [1]                  # Ladder variants count


def test_last_opponent_reports_ranked_mode(predictor):
    app, _, _ = make(predictor)
    assert app.get("/api/players/%232PP/last-opponent").json()["mode"] == "ranked"


def test_odd_game_mode_values_are_ignored_safely():
    client = FakeClient()
    client.get_battle_log = lambda tag: [battle(1, kind="trail") | {"gameMode": "Challenge"},
                                         battle(2, kind="trail") | {"gameMode": {"name": 5}},
                                         battle(3, kind="trail") | {"gameMode": None}]
    app, _, _ = make(client=client)
    r = app.get("/api/players/%232PP/decks")
    assert r.status_code == 200 and r.json()["modes"] == {"ranked": [], "trophy": [], "challenge": []}


def test_predict_accepts_a_deck_with_a_hidden_hero(predictor):
    app, _, _ = make(predictor)
    r = app.post("/api/predict", json={"deck_a": list(MY_DECK)[:7], "deck_b": list(OPP_DECK)})
    assert r.status_code == 200 and "seven cards" in r.json()["note"]


def test_recent_decks_include_details_and_last_played():
    app, _, _ = make()
    d = app.get("/api/players/%239QJ/decks").json()["modes"]["ranked"][0]
    assert d["deck"]["summary"]["avg_elixir"] == 3.5 and d["deck"]["summary"]["maxed"] == 8
    assert d["last_played"].startswith("2026-10-30T01:05")


def test_key_cards_say_whose_deck_they_are_in(predictor):
    app, _, _ = make(predictor)
    data = app.post("/api/predict", json={"deck_a": list(MY_DECK), "deck_b": list(OPP_DECK)}).json()
    assert {k["deck"] for k in data["key_cards"]} <= {"yours", "theirs"}
    assert all(set(k) == {"card", "deck", "effect"} for k in data["key_cards"])


# --- win rates by tier ------------------------------------------------------------------


OPP_7 = OPP_DECK[:7] + (card_id(46),)                  # shares 7 of 8 cards with OPP_DECK
MY_6 = MY_DECK[:6] + (card_id(47), card_id(48))         # shares 6 of 8 cards with MY_DECK


def _tier_rows():
    """(winner_side, deck0, rank0, deck1, rank1) rows; None = rank unknown."""
    rows = []
    rows += [(0, MY_DECK, 50, OPP_DECK, 150)] * 6      # top 200 game, I win
    rows += [(1, MY_DECK, 50, OPP_DECK, 150)] * 2      # top 200 game, they win
    rows += [(0, MY_DECK, 500, OPP_DECK, 900)] * 3     # top 1,000 only
    rows += [(1, OPP_DECK, 3000, MY_DECK, 9000)] * 4   # outside the tiers; sides swapped, I win
    rows += [(1, MY_DECK, None, OPP_7, None)] * 2      # similar opponent deck, ranks unknown
    rows += [(0, MY_6, 100, OPP_DECK, 100)]            # similar deck of mine, top 200
    rows += [(0, MY_DECK, 50, MY_DECK, 60)] * 5        # mirror match
    return rows


def _cells(breakdown, level):
    row = next(r for r in breakdown["levels"] if r["level"] == level)
    return {c["tier"]: c for c in row["cells"]}


def test_breakdown_by_match_level_and_tier(predictor):
    from decklens.web.tiers import TierStats

    stats = TierStats(predictor.archetypes, _tier_rows)
    i, j = (int(x) for x in predictor.archetypes.assign([MY_DECK, OPP_DECK]))
    b = stats.matchup(MY_DECK, OPP_DECK, i, j)
    assert [r["level"] for r in b["levels"]] == ["exact", "7", "6", "archetype"]

    def record(level):
        return {t: (c["wins"], c["games"]) for t, c in _cells(b, level).items()}

    assert record("exact") == {200: (6, 8), 1000: (9, 11), None: (13, 15)}  # swapped sides counted
    assert record("7") == {200: (6, 8), 1000: (9, 11), None: (13, 17)}      # + the 7-of-8 games
    assert record("6") == {200: (7, 9), 1000: (10, 12), None: (14, 18)}     # + my 6-of-8 deck
    arch = record("archetype")
    assert all(arch[t][1] >= record("exact")[t][1] for t in arch)           # loosest level
    top200 = _cells(b, "exact")[200]
    assert top200["range"][0] < top200["rate"] < top200["range"][1]


def test_breakdown_mirrors_and_hidden_heroes(predictor):
    from decklens.web.tiers import TierStats

    stats = TierStats(predictor.archetypes, _tier_rows)
    i = int(predictor.archetypes.assign([MY_DECK])[0])
    mirror = stats.matchup(MY_DECK, MY_DECK, i, i)
    assert _cells(mirror, "exact")[200] == {"tier": 200, "games": 5, "mirror": True}

    # Their Hero was hidden: 7 known cards, so "exact" means all 7 of those match.
    j = int(predictor.archetypes.assign([OPP_DECK])[0])
    hidden = stats.matchup(MY_DECK, OPP_DECK[:7], i, j)
    assert _cells(hidden, "exact")[None]["games"] == 17   # OPP_DECK and OPP_7 both qualify


def test_breakdown_is_cached_and_survives_database_trouble(predictor):
    from decklens.web.tiers import TierStats

    calls = []
    now = [0.0]

    def flaky():
        calls.append(1)
        if len(calls) > 1:
            raise RuntimeError("database down")
        return _tier_rows()

    stats = TierStats(predictor.archetypes, flaky, ttl=600, clock=lambda: now[0])
    first = stats.matchup(MY_DECK, OPP_DECK, 0, 1)
    stats.matchup(MY_DECK, OPP_DECK, 0, 1)
    assert len(calls) == 1          # cached
    now[0] = 601
    assert stats.matchup(MY_DECK, OPP_DECK, 0, 1) == first and len(calls) == 2  # old data kept
    empty = TierStats(predictor.archetypes, list)
    assert _cells(empty.matchup(MY_DECK, OPP_DECK, 0, 1), "exact")[None]["games"] == 0


def test_predict_includes_breakdown_when_available(predictor):
    from decklens.web.tiers import TierStats

    predictor.tiers = TierStats(predictor.archetypes, _tier_rows)
    try:
        app, _, _ = make(predictor)
        data = app.post("/api/predict", json={"deck_a": list(MY_DECK),
                                              "deck_b": list(OPP_DECK)}).json()
        assert [c["tier"] for c in data["breakdown"]["levels"][0]["cells"]] == [200, 1000, None]
        assert data["breakdown"]["window_days"] == 14
    finally:
        predictor.tiers = None


def test_evolutions_and_heroes_become_form_tokens():
    from decklens.battles import form_tokens
    from decklens.web.services import validate_deck

    raw = [{"id": 26000003, "evolutionLevel": 1},   # Evolution slot: Evo Giant
           {"id": 26000004, "evolutionLevel": 2},   # Hero slot: a Hero
           {"id": 26000005, "evolutionLevel": 3},   # Wild slot, both unlocked: unknown, no token
           {"id": 26000006, "evolutionLevel": 1}]   # ordinary slot: plain card
    assert form_tokens(raw) == (1_026_000_003, 2_026_000_004)

    deck = list(range(26000001, 26000009))
    known = set(deck)
    assert validate_deck(deck + [1_026_000_003], known)[-1] == 1_026_000_003
    for bad in ([1_026_000_099],                                 # evo of a card not in the deck
                [1_026_000_001, 1_026_000_002, 1_026_000_003, 2_026_000_004]):  # 4 forms
        with pytest.raises(ValueError):
            validate_deck(deck + bad, known)


def test_evo_and_plain_versions_are_different_matchups(predictor):
    from decklens.web.tiers import TierStats

    evo_opp = OPP_DECK + (OPP_DECK[0] + 1_000_000_000,)
    rows = ([(0, MY_DECK, None, OPP_DECK, None)] * 6
            + [(1, MY_DECK, None, evo_opp, None)] * 4)       # the Evo version beats me
    stats = TierStats(predictor.archetypes, lambda: rows)
    i, j = (int(x) for x in predictor.archetypes.assign([MY_DECK, evo_opp]))
    vs_evo = stats.matchup(MY_DECK, evo_opp, i, j)
    assert {lvl: (_cells(vs_evo, lvl)[None].get("wins"), _cells(vs_evo, lvl)[None]["games"])
            for lvl in ("exact", "7")} == {"exact": (0, 4), "7": (0, 4)}


LOG, ROCKET, FIREBALL = 28000011, 28000005, 28000000   # spells, by the game's id ranges
MY_S = MY_DECK[:7] + (LOG,)
THEM_ROCKET = OPP_DECK[:7] + (ROCKET,)
THEM_FIREBALL = OPP_DECK[:7] + (FIREBALL,)    # 7 of 8 the same, but a different matchup


def _spell_rows():
    return ([(1, MY_S, None, THEM_ROCKET, None)] * 5 + [(0, MY_S, None, THEM_ROCKET, None)] * 2
            + [(0, MY_S, None, THEM_FIREBALL, None)] * 10)


def test_similar_decks_must_keep_the_same_spells(predictor):
    from decklens.web.tiers import TierStats

    stats = TierStats(predictor.archetypes, _spell_rows)
    i, j = (int(x) for x in predictor.archetypes.assign([MY_S, THEM_ROCKET]))
    b = stats.matchup(MY_S, THEM_ROCKET, i, j)
    for level in ("exact", "7", "6", "archetype"):
        cell = _cells(b, level)[None]
        assert (cell.get("wins"), cell["games"]) in ((2, 7), (None, 7)), level  # Fireball games left out


def test_headline_uses_the_most_specific_level_pulled_toward_the_archetype():
    from decklens.web.services import headline

    breakdown = {"levels": [
        {"level": "exact", "cells": [{"tier": None, "games": 7, "wins": 2}]},
        {"level": "7", "cells": [{"tier": None, "games": 22, "wins": 12}]},
    ]}
    out = headline(breakdown, 0.53)
    assert out["win_chance"] == pytest.approx((2 + 5.3) / 17, abs=0.001)   # 43%
    assert out["based_on"]["level"] == "exact" and out["estimate_chance"] == 0.53
    breakdown["levels"][0]["cells"][0]["games"] = 4                        # too few: next level
    assert headline(breakdown, 0.53)["based_on"]["level"] == "7"
    assert headline({"levels": []}, 0.53) == {}


def test_predict_headline_reflects_the_spell_matchup(predictor):
    from decklens.web.tiers import TierStats

    predictor.tiers = TierStats(predictor.archetypes, _spell_rows)
    try:
        m = predictor.predict(MY_S, THEM_ROCKET)
        assert m["based_on"]["games"] == 7 and not m["mirror"]   # 2 wins in 7 games
        assert m["win_chance"] < m["estimate_chance"]            # pulls the headline down
    finally:
        predictor.tiers = None


# --- card images ------------------------------------------------------------------------

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
GOOD_URL = "https://api-assets.clashroyale.com/cards/300/abcDEF123_-xyz.png"
EVO_URL = "https://api-assets.clashroyale.com/cardevolutions/300/evoXYZ.png"
HERO_URL = "https://api-assets.clashroyale.com/cardheroes/300/heroXYZ.png"


@pytest.mark.parametrize("url, ok", [
    (GOOD_URL, True),
    (EVO_URL, True),
    ("http://api-assets.clashroyale.com/cards/300/a.png", False),          # not https
    ("https://evil.example/cards/300/a.png", False),                        # other host
    ("https://api-assets.clashroyale.com.evil.example/cards/300/a.png", False),
    ("https://user@api-assets.clashroyale.com/cards/300/a.png", False),     # credentials trick
    ("https://api-assets.clashroyale.com:8443/cards/300/a.png", False),     # other port
    ("https://api-assets.clashroyale.com/cards/300/../../etc.png", False),  # path tricks
    ("https://api-assets.clashroyale.com/cards/300/a.png?x=1", False),      # query string
    ("https://169.254.169.254/latest/meta-data", False),                    # cloud metadata
    (None, False),
])
def test_only_official_card_image_urls_are_allowed(url, ok):
    from decklens.web.cards import safe_asset_url

    assert (safe_asset_url(url) is not None) == ok


class FakeAssetResponse:
    def __init__(self, status=200, ctype="image/png", body=PNG):
        self.status_code, self.headers, self.body = status, {"Content-Type": ctype}, body
        self.closed = False

    def iter_content(self, chunk_size):
        for k in range(0, len(self.body), chunk_size):
            yield self.body[k:k + chunk_size]

    def close(self):
        self.closed = True


class FakeAssetSession:
    def __init__(self, response=None):
        self.response = response or FakeAssetResponse()
        self.headers = {}
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class CatalogClient(FakeClient):
    def __init__(self, error=None):
        super().__init__()
        self.card_error = error

    def get_cards(self):
        self.calls.append(("cards", None))
        if self.card_error:
            raise self.card_error
        return [
            {"id": 26000010, "name": "Card 10",
             "iconUrls": {"medium": GOOD_URL, "evolutionMedium": EVO_URL, "heroMedium": HERO_URL}},
            {"id": 26000012, "name": "Card 12",
             "iconUrls": {"medium": GOOD_URL.replace("abc", "twelve")}},
            {"id": 26000011, "name": "Card 11",
             "iconUrls": {"medium": "https://evil.example/cards/300/x.png"}},
        ]


def _images(response=None, client=None):
    from decklens.web.cards import CardImages

    session = FakeAssetSession(response)
    return CardImages(client or CatalogClient(), session=session), session


def test_card_image_is_fetched_safely_and_cached():
    images, session = _images()
    assert images.image(26000010) == PNG
    assert images.image(26000010) == PNG
    assert len(session.calls) == 1                                  # cached
    url, kwargs = session.calls[0]
    assert url == GOOD_URL and kwargs["allow_redirects"] is False and kwargs["timeout"] == 5
    assert "Authorization" not in kwargs.get("headers", {})         # API key never sent
    assert "Authorization" not in session.headers
    assert images.image(26000010, form="evo") == PNG
    assert session.calls[-1][0] == EVO_URL
    assert images.image(26000010, form="hero") == PNG
    assert session.calls[-1][0] == HERO_URL
    assert images.image(26000012, form="hero") == PNG     # no hero art: plain card instead
    assert session.calls[-1][0] == GOOD_URL.replace("abc", "twelve")


def test_new_evolution_art_shows_up_without_a_restart():
    from decklens.web.cards import CardImages

    class ReleaseDay(CatalogClient):
        released = False

        def get_cards(self):
            cards = super().get_cards()
            if not self.released:  # before the update: Card 12 has no evolution art yet
                return cards
            cards[1]["iconUrls"]["evolutionMedium"] = EVO_URL
            return cards

    client, now, session = ReleaseDay(), [0.0], FakeAssetSession()
    images = CardImages(client, session=session, clock=lambda: now[0])
    assert images.image(26000012, form="evo") == PNG and not images.has_art(26000012, "evo")
    client.released = True
    now[0] = 300
    images.image(26000012, form="evo")
    assert session.calls[-1][0] != EVO_URL               # re-read at most every 10 minutes
    now[0] = 700
    assert images.image(26000012, form="evo") == PNG and session.calls[-1][0] == EVO_URL
    assert images.has_art(26000012, "evo")
    reads = sum(1 for c in client.calls if c[0] == "cards")
    for _ in range(5):
        images.image(99999999)                            # made-up ids don't re-read the list
    assert sum(1 for c in client.calls if c[0] == "cards") <= reads + 1


def test_card_with_unsafe_url_in_catalog_is_skipped():
    images, session = _images()
    assert images.image(26000011) is None and session.calls == []


@pytest.mark.parametrize("response", [
    FakeAssetResponse(status=302),                                 # redirect refused
    FakeAssetResponse(ctype="text/html"),                          # not an image
    FakeAssetResponse(body=b"<svg onload=alert(1)>"),              # PNG header lie
    FakeAssetResponse(body=b"\x89PNG\r\n\x1a\n" + b"\x00" * 300_000),  # too big
])
def test_bad_image_responses_are_rejected(response):
    images, _ = _images(response)
    assert images.image(26000010) is None
    assert response.closed


def test_card_image_route():
    images, _ = _images()
    services = Services(client=FakeClient(), images=images)
    app = TestClient(create_app(services), raise_server_exceptions=False)
    ok = app.get("/cards/26000010.png")
    assert ok.status_code == 200 and ok.content == PNG
    assert ok.headers["content-type"] == "image/png"
    assert ok.headers["cache-control"] == "public, max-age=86400"
    assert ok.headers["x-content-type-options"] == "nosniff"
    assert app.get("/cards/26000010.png?form=hero").status_code == 200
    fallback = app.get("/cards/26000012.png?form=evo")    # no evo art yet: plain card, briefly cached
    assert fallback.status_code == 200 and fallback.headers["cache-control"] == "public, max-age=600"
    for bad in ("/cards/99.png", "/cards/abc.png", "/cards/-5.png", "/cards/26000011.png",
                "/cards/26000010.png?form=../../x", "/cards/26000010.png?form=both"):
        assert app.get(bad).status_code in (404, 422)


def test_card_images_degrade_gracefully():
    images, _ = _images(client=CatalogClient(error=ApiError("forbidden", 403)))
    app = TestClient(create_app(Services(client=FakeClient(), images=images)),
                     raise_server_exceptions=False)
    assert app.get("/cards/26000010.png").status_code == 404
    no_images = TestClient(create_app(Services(client=FakeClient())))
    assert no_images.get("/cards/26000010.png").status_code == 404


# --- live clan lookup ---------------------------------------------------------------


def _seen(minutes_ago):
    from datetime import datetime, timedelta, timezone

    t = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return t.strftime("%Y%m%dT%H%M%S.000Z")


class ClanClient(FakeClient):
    def __init__(self, error=None):
        super().__init__()
        self.clan_error = error

    def search_clans(self, name, limit=10):
        self.calls.append(("clans", name))
        if self.clan_error:
            raise self.clan_error
        if len(name.strip()) < 3:
            raise ValueError("clan names must be 3-50 characters")
        return [
            {"tag": "#2QQ", "name": "Eatropa Theotôn 2"},                    # close, not exact
            {"tag": "#9YY", "name": "Eatropa Theotôn"},                      # exact: checked first
            {"tag": "../evil", "name": "Eatropa Theotôn"},                   # bad tag: skipped
        ] + [{"tag": f"#{c}2{c}", "name": f"Eatropa {c}"} for c in "GJLQRUV"]

    def get_clan_members(self, tag):
        self.calls.append(("members", tag))
        if tag == "#9YY":
            return [
                {"tag": "#V8Q9LV8GC", "name": "+Kaua", "lastSeen": _seen(1), "trophies": 11862},
                {"tag": "#2PP", "name": HOSTILE, "lastSeen": _seen(300)},
                {"tag": "#8YY", "name": "🔥🔥", "lastSeen": "garbage"},
                {"tag": "bad tag", "name": "x", "lastSeen": _seen(0)},
            ]
        return [{"tag": "#LLL", "name": "Someone", "lastSeen": _seen(40)}]


def test_live_clan_lookup_with_a_name_checks_more_clans_and_stops_when_found():
    class ManyClans(ClanClient):
        def search_clans(self, name, limit=10):
            self.calls.append(("clans", name, limit))
            return [{"tag": f"#{a}{b}{a}{b}", "name": "YEET"} for a in "289PQ" for b in "PQRUVY"]

        def get_clan_members(self, tag):
            self.calls.append(("members", tag))
            if tag == "#9P9P":   # the 13th YEET clan: past the old limits of 5 and 12
                return [{"tag": "#V8Q9LV8GC", "name": "DaHveed", "lastSeen": _seen(1)}]
            return [{"tag": "#LLL", "name": "Matt", "lastSeen": _seen(2)}]

    client = ManyClans()
    app, _, _ = make(client=client)
    data = app.get("/api/clan-lookup", params={"clan": "YEET", "name": "DaHveed"}).json()
    assert [p["name"] for p in data["players"]] == ["DaHveed"] and data["players"][0]["active"]
    assert len(data["clans_checked"]) == 16                     # stopped after the batch that found them
    assert ("clans", "YEET", 50) in client.calls                     # asked for more clans
    # Without a name, still only the first 5 clans.
    client.calls.clear()
    assert len(app.get("/api/clan-lookup", params={"clan": "YEET"}).json()["clans_checked"]) == 5


def test_live_clan_lookup_uses_clans_decklens_knows_when_the_name_was_misread():
    """The real case: "RICH ESPORTS" read as "RICHESPORTS". The game finds no clan
    by that name, but DeckLens knows RICH ESPORTS (and its tag) from its players."""
    class NoSuchName(ClanClient):
        def search_clans(self, name, limit=10):
            self.calls.append(("clans", name))
            return []

        def get_clan_members(self, tag):
            self.calls.append(("members", tag))
            if tag == "#Q9JRV2YP":
                return [{"tag": "#V8Q9LV8GC", "name": "卡罗·沃苏吉 I KARO", "lastSeen": _seen(0)},
                        {"tag": "#8QL98Q2L", "name": "Rolex", "lastSeen": _seen(30)}]
            return []

    client = NoSuchName()
    app, _, conn = make(client=client, rows=[("#Q9JRV2YP", "RICH ESPORTS", 9, 1.0)])
    data = app.get("/api/clan-lookup", params={"clan": "RICHESPORTS", "name": "卡罗沃苏吉KARD"}).json()
    assert data["clans_checked"] == ["RICH ESPORTS"] and data["clans_found"] == 1
    assert [p["name"] for p in data["players"]] == ["卡罗·沃苏吉 I KARO"]
    assert data["players"][0]["active"]
    assert conn.executed[-1]["key"] == "richesports"                  # spaces don't matter
    assert ("members", "#Q9JRV2YP") in client.calls


def test_live_clan_lookup_puts_whoever_is_online_first():
    client = ClanClient()
    app, _, _ = make(client=client)
    data = app.get("/api/clan-lookup", params={"clan": "eatropa theoton"}).json()
    assert data["clans_checked"][0] == "Eatropa Theotôn" and len(data["clans_checked"]) == 5
    assert data["clans_found"] == 9                                    # the bad tag was dropped
    first = data["players"][0]
    assert (first["tag"], first["active"], first["trophies"]) == ("#V8Q9LV8GC", True, 11862)
    assert first["last_seen_minutes"] <= 2
    assert data["players"][-1]["tag"] == "#8YY" and data["players"][-1]["last_seen_minutes"] is None
    assert all(p["tag"] != "BAD TAG" for p in data["players"])
    assert len(data["players"]) <= 10
    member_calls = [c for c in client.calls if c[0] == "members"]
    assert len(member_calls) == 5                                      # at most 5 clans checked

    again = app.get("/api/clan-lookup", params={"clan": "Eatropa Theoton"})
    assert again.status_code == 200 and len(client.calls) == len(member_calls) + 1  # cached


def test_live_clan_lookup_can_filter_by_name_letters():
    app, _, _ = make(client=ClanClient())
    data = app.get("/api/clan-lookup", params={"clan": "eatropa", "name": "kau"}).json()
    assert [p["tag"] for p in data["players"]] == ["#V8Q9LV8GC"]


@pytest.mark.parametrize("params, status", [
    ({"clan": "ab"}, 422), ({"clan": "   ab   "}, 400), ({"clan": "x" * 51}, 422),
    ({}, 422), ({"clan": "eatropa", "name": "x" * 41}, 422),
])
def test_live_clan_lookup_input_validation(params, status):
    app, _, _ = make(client=ClanClient())
    assert app.get("/api/clan-lookup", params=params).status_code == status


@pytest.mark.parametrize("error, status", [
    (ApiError("403 forbidden", 403), 503), (ApiError("HTTP 500", 500), 502),
    (NotFoundError("not found", 404), 404),
])
def test_live_clan_lookup_upstream_errors(error, status):
    app, _, _ = make(client=ClanClient(error=error))
    response = app.get("/api/clan-lookup", params={"clan": "eatropa"})
    assert response.status_code == status and "token" not in response.text.lower()


def test_live_clan_lookup_has_its_own_tight_rate_limit():
    app, _, _ = make(client=ClanClient())
    codes = [app.get("/api/clan-lookup", params={"clan": f"clan {i}"}).status_code
             for i in range(7)]
    assert codes[:6] == [200] * 6 and codes[6] == 429


def test_most_recent_deck_comes_first_not_the_most_played():
    def played(minute, deck):
        b = battle(minute)
        b["team"][0]["cards"] = cards(deck)
        return b

    old_favourite = MY_DECK
    new_deck = OPP_DECK
    third = core_cards(2) + (card_id(46), card_id(47), card_id(48))
    fourth = core_cards(3) + (card_id(49), card_id(50), card_id(51))
    log = ([played(9, new_deck)]                                       # newest game
           + [played(m, old_favourite) for m in (8, 7, 6, 5)]          # played the most
           + [played(4, third), played(3, fourth)])                    # oldest, beyond top 3
    client = FakeClient()
    client.get_battle_log = lambda tag: log
    app, _, _ = make(client=client)
    decks = app.get("/api/players/%232PP/decks").json()["modes"]["ranked"]
    assert [d["games"] for d in decks] == [1, 4, 1]                    # newest, then the favourite
    assert decks[0]["last_played"].startswith("2026-10-30T01:09")
    assert decks[1]["last_played"].startswith("2026-10-30T01:08")      # its most recent game
    assert decks[2]["last_played"].startswith("2026-10-30T01:04")      # fourth deck dropped


def test_live_clan_lookup_forgives_typos_in_name_and_clan():
    app, _, _ = make(client=ClanClient())
    data = app.get("/api/clan-lookup", params={"clan": "eatrpa theoton", "name": "kua"}).json()
    assert "Eatropa Theotôn" in data["clans_checked"][:2]     # closest clan names checked first
    assert data["players"][0]["tag"] == "#V8Q9LV8GC"          # "kua" still finds +Kaua
    assert all(p["name"] != "Someone" for p in data["players"])  # unrelated names left out


def test_live_clan_lookup_without_a_name_is_most_recent_first():
    app, _, _ = make(client=ClanClient())
    minutes = [p["last_seen_minutes"] for p in
               app.get("/api/clan-lookup", params={"clan": "eatropa"}).json()["players"]]
    known = [m for m in minutes if m is not None]
    assert known == sorted(known) and minutes[-1] is None


# --- screen watcher hand-off --------------------------------------------------------


def test_watcher_reports_reach_the_page_as_text():
    app, services, _ = make()
    assert app.get("/api/watch").json()["latest"] is None
    sent = app.post("/api/watch", json={"name": "  FIOReNt ", "clan": "Sunny"})
    assert sent.status_code == 200
    latest = app.get("/api/watch").json()["latest"]
    assert (latest["name"], latest["clan"], latest["id"]) == ("FIOReNt", "Sunny", sent.json()["id"])
    app.post("/api/watch", json={"name": "Kaua", "clan": ""})
    assert app.get("/api/watch").json()["latest"]["clan"] is None and \
        app.get("/api/watch").json()["latest"]["name"] == "Kaua"


@pytest.mark.parametrize("body, status", [
    ({}, 400), ({"name": ""}, 400), ({"name": " ", "clan": ""}, 400),   # nothing to search by
    ({"rating": 2216}, 400),                     # a rating alone says too little
    ({"name": "x" * 41}, 422), ({"name": "a", "clan": "x" * 51}, 422),
    ({"name": "a", "rating": -1}, 422), ({"name": "a", "rating": 99999}, 422),
    ({"name": "a", "image": "data:..."}, 422),   # text only: nothing else accepted
])
def test_watcher_reports_are_validated(body, status):
    app, _, _ = make()
    assert app.post("/api/watch", json=body).status_code == status


def test_watcher_report_with_unreadable_name_keeps_clan_and_rating():
    app, _, _ = make()
    assert app.post("/api/watch", json={"name": None, "clan": "STS", "rating": 2216}).status_code == 200
    latest = app.get("/api/watch").json()["latest"]
    assert (latest["name"], latest["clan"], latest["rating"]) == (None, "STS", 2216)


def test_watcher_battle_notes_carry_no_text():
    app, _, _ = make()
    assert app.post("/api/watch", json={"kind": "unreadable", "name": "ignored"}).status_code == 200
    latest = app.get("/api/watch").json()["latest"]
    assert latest["kind"] == "unreadable" and latest["name"] is None and latest["clan"] is None
    assert app.post("/api/watch", json={"kind": "ended"}).status_code == 200
    assert app.get("/api/watch").json()["latest"]["kind"] == "ended"
    assert app.post("/api/watch", json={"kind": "screenshot"}).status_code == 422
    app.post("/api/watch", json={"name": "Kaua"})
    assert app.get("/api/watch").json()["latest"]["kind"] == "opponent"


def test_watcher_reports_expire():
    from decklens.web.watch import WatchBoard

    now = [1000.0]
    board = WatchBoard(clock=lambda: now[0])
    board.report("david", "Florent", "Sunny")
    assert board.latest("david")["age_seconds"] == 0
    assert board.latest("someone-else") is None
    now[0] += 601
    assert board.latest("david") is None


def test_watcher_needs_same_site_origin():
    services = Services(client=FakeClient())
    app = TestClient(create_app(services), raise_server_exceptions=False)
    assert app.post("/api/watch", json={"name": "x"}).status_code == 403


# --- desktop app ----------------------------------------------------------------------


def test_desktop_mode_flag_and_quit_button():
    app, services, _ = make()
    assert app.get("/api/me").json()["desktop"] is False
    assert app.post("/api/desktop/quit").status_code == 404        # not inside the app
    quits = []
    services.quit_app = lambda: quits.append(1)
    assert app.get("/api/me").json()["desktop"] is True
    assert app.post("/api/desktop/quit").json() == {"ok": True} and quits == [1]


def test_quit_needs_same_site_request():
    services = Services(client=FakeClient(), quit_app=lambda: None)
    app = TestClient(create_app(services), raise_server_exceptions=False)
    assert app.post("/api/desktop/quit").status_code == 403


def test_desktop_watcher_reports_reach_the_logged_in_user():
    from decklens.web.watch import SCREEN_KEY

    app, services, _ = make()
    services.watch.report(SCREEN_KEY, "FIOReNt", "Sunny")
    assert app.get("/api/watch").json()["latest"]["name"] == "FIOReNt"
    app.post("/api/watch", json={"name": "Newer", "clan": None})     # newest report wins
    assert app.get("/api/watch").json()["latest"]["name"] == "Newer"


def test_favicon_is_served():
    app, _, _ = make()
    r = app.get("/favicon.ico")
    assert r.status_code == 200 and r.headers["content-type"] == "image/x-icon"
    assert r.content[:4] == b"\x00\x00\x01\x00"                  # an .ico file



def test_watcher_status_reaches_the_page_and_goes_stale():
    from decklens.web.watch import SCREEN_KEY, WatchBoard

    app, services, _ = make()
    assert app.get("/api/watch").json() == {"latest": None, "watcher": None}   # not running
    assert app.post("/api/watch/status", json={"state": "connected", "detail": "David's A53"}
                    ).status_code == 200
    assert app.get("/api/watch").json()["watcher"] == {"state": "connected",
                                                       "detail": "David's A53"}

    now = [1000.0]
    board = WatchBoard(clock=lambda: now[0])
    board.set_status(SCREEN_KEY, "connected", "David's A53")          # the desktop app's watcher
    assert board.status("david")["state"] == "connected"
    now[0] += 11                                                       # went quiet: not running
    assert board.status("david") is None


@pytest.mark.parametrize("body", [
    {"state": "hacked"}, {"state": "connected", "detail": "x" * 81}, {},
    {"state": "connected", "extra": 1},
])
def test_watcher_status_is_validated(body):
    app, _, _ = make()
    assert app.post("/api/watch/status", json=body).status_code == 422


@pytest.mark.parametrize("card_id, kind", [
    (26000021, "troop"), (27000006, "building"), (28000011, "spell"),
    (159000000, None), (9, None),   # tower troops and unknown ids get no kind
])
def test_card_kind_comes_from_the_id_range(card_id, kind):
    from decklens.web.services import _cards, card_kind

    assert card_kind(card_id) == kind
    assert _cards([{"id": card_id, "name": "X"}])[0]["kind"] == kind


def test_trees_give_the_estimate_and_see_inside_one_archetype():
    from decklens.model import TreeMatchupModel

    matchups, names, _ = make_archetype_matchups(3000, seed=4)
    vocab = CardVocab.build(matchups, names=names)
    p = Predictor(ArchetypeModel.fit(vocab, matchups, 6, 20.0),
                  trees=TreeMatchupModel.from_json(TreeMatchupModel.fit(vocab, matchups).to_json()))
    same_style = MY_DECK[:7] + (card_id(49),)    # same archetype, one different card
    m = p.predict(MY_DECK, same_style)
    assert m["estimate"] == "model" and m["mirror"] is False
    assert m["win_chance"] == pytest.approx(p.model_chance(MY_DECK, same_style), abs=1e-3)
    assert "coin flip" not in m["note"]
    assert p.predict(MY_DECK, MY_DECK)["win_chance"] == 0.5     # identical decks stay even
