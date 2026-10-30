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
    assert data["modes"]["challenge"] == [] and data["recent_modes"] == {"Ranked": 2}


def test_last_opponent_without_ranked_battles():
    client = FakeClient()
    client.get_battle_log = lambda tag: [battle(1, kind="trail")]
    app, _, _ = make(client=client)
    assert app.get("/api/players/%232PP/last-opponent").status_code == 404


# --- search --------------------------------------------------------------------------


def test_search_normalises_and_escapes(predictor):
    rows = [("#9QJ", HOSTILE, "Clan", 812, 2400)]
    app, _, conn = make(rows=rows)
    response = app.get("/api/search", params={"name": "🔥Dípo%_", "near_rating": 2136})
    assert response.status_code == 200
    assert response.json()["results"][0]["tag"] == "#9QJ"
    params = conn.executed[0]
    assert params == {"pattern": "dipo%", "key": "dipo", "near": 2136, "limit": 10}  # no wildcards
    assert response.json()["results"][0]["rating"] == 2400


@pytest.mark.parametrize("params, status", [
    ({"name": "🔥"}, 400),               # nothing searchable left after normalising
    ({"name": "x" * 41}, 422),           # too long
    ({"name": "dipo", "near_rating": -1}, 422),
    ({"name": "dipo", "near_rating": 99999}, 422),
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
    assert card_ids(out) == [100 + i for i in range(7)]
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
    assert data["modes"] == {"ranked": [], "challenge": []}
    assert data["recent_modes"] == {"Trophy Road": 2, "Other challenges": 1, "Other modes": 1}


CHALLENGE = {"id": 72000474, "name": "Challenge_AllCards_EventDeck_NoSet"}


def test_challenge_decks_get_their_own_tab_and_other_trail_modes_are_ignored():
    def challenge(minute, me_won=True):
        return battle(minute, me_won=me_won, kind="trail") | {"gameMode": CHALLENGE}

    trophy_road = battle(7, kind="trail") | {"gameMode": {"id": 72000006, "name": "Ladder"}}
    log = [trophy_road, challenge(6), challenge(5, me_won=False), challenge(4), battle(3)]
    client = FakeClient()
    client.get_battle_log = lambda tag: log
    app, _, _ = make(client=client)

    data = app.get("/api/players/%232PP/decks").json()
    assert [d["games"] for d in data["modes"]["challenge"]] == [3]
    assert data["modes"]["challenge"][0]["wins"] == 2
    assert [d["games"] for d in data["modes"]["ranked"]] == [1]
    assert data["recent_modes"] == {"20-win challenge": 3, "Trophy Road": 1, "Ranked": 1}

    last = app.get("/api/players/%232PP/last-opponent").json()
    assert last["mode"] == "challenge" and last["your_result"] == "won"  # Trophy Road skipped


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
    assert r.status_code == 200 and r.json()["modes"] == {"ranked": [], "challenge": []}


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
