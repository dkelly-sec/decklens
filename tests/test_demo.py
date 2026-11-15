"""Demo mode (python -m decklens.web --demo): the real app on sample data."""

import pytest
from fastapi.testclient import TestClient

from decklens.archetypes import WIN_CONDITIONS
from decklens.config import ConfigError
from decklens.web import demo
from decklens.web.app import create_demo_app

YOU = "%232PPGJQ8V"


@pytest.fixture(scope="module")
def app():
    # Built once (it fits a model), so it answers to localhost, not the TestClient's
    # default "testserver" that conftest allows one test at a time.
    return TestClient(create_demo_app(), base_url="http://localhost",
                      headers={"origin": "http://localhost"})


def test_the_page_is_told_it_is_sample_data(app):
    assert app.get("/api/me").json() == {"username": None, "login": False, "desktop": False,
                                         "demo": demo.YOU}
    assert 'id="demo-mark"' in app.get("/").text


def test_an_ordinary_server_is_not_a_demo():
    from test_web import make

    assert make()[0].get("/api/me").json()["demo"] is None


def test_sample_player_has_a_deck_a_session_and_a_last_opponent(app):
    me = app.get(f"/api/players/{YOU}").json()
    assert me["name"] == "Demo Player" and len(me["deck"]["cards"]) == 8 and me["ultimate"]
    session = app.get(f"/api/players/{YOU}/session").json()
    assert session["games"] >= 10
    last = app.get(f"/api/players/{YOU}/last-opponent").json()
    assert last["name"] == "GolemGrinder"
    # The X-Bow player meets Golem and Lightning: the known-counters warning has something to show.
    assert last["matchup"]["counters"]["against_you"] == [
        {"win_condition": "X-Bow", "cards": ["Lightning", "Golem"]}]
    assert last["matchup"]["breakdown"]["levels"]  # the battle table is filled in too


def test_every_page_has_something_to_show(app):
    assert app.get(f"/api/players/{YOU}/record").json()["games"] >= 10
    assert app.get(f"/api/players/{YOU}/country-rank").json()["country_rank"]["country"] == "Australia"
    assert len(app.get("/api/meta").json()["archetypes"]) == len(demo._DECKS)
    found = app.get("/api/clan-lookup", params={"clan": "Cycle Club"}).json()
    assert {p["name"] for p in found["players"]} == {"hog_2.6_only", "LavaLena"}


def test_a_tag_outside_the_sample_is_just_not_found(app):
    response = app.get("/api/players/%239QQQ")
    assert response.status_code == 404 and response.json() == {"detail": "No player with that tag"}


def test_sample_decks_are_eight_known_cards_led_by_a_win_condition():
    for names, swap in demo._DECKS.values():
        assert len(set(names)) == 8 and swap not in names
        assert all(n in demo._BY_NAME for n in (*names, swap))
        assert names[0] in WIN_CONDITIONS


def test_demo_has_no_login_so_it_refuses_a_public_server(monkeypatch):
    monkeypatch.setenv("DECKLENS_HTTPS", "1")
    with pytest.raises(ConfigError):
        create_demo_app()
