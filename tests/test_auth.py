import time

import pytest
from fastapi.testclient import TestClient

from decklens.web.app import Services, create_app
from decklens.web.auth import (
    Auth, LoginGuard, SessionSigner, User, check_new_password, hash_password,
    normalise_username, verify_password,
)

SECRET = "x" * 48
FAST = 14  # cheap scrypt cost for tests; real accounts use 17
PASSWORD = "correct horse battery staple"


def user(name="david", password=PASSWORD, version=1, disabled=False):
    return User(name, hash_password(password, log_n=FAST), version, disabled)


# --- passwords -------------------------------------------------------------------------


def test_password_hash_round_trip_and_unique_salts():
    a, b = hash_password(PASSWORD, log_n=FAST), hash_password(PASSWORD, log_n=FAST)
    assert a != b and a.startswith("scrypt$14$8$1$")     # fresh salt every time
    assert verify_password(PASSWORD, a) and verify_password(PASSWORD, b)
    assert not verify_password(PASSWORD + "!", a)
    assert not verify_password("", a)


def test_default_cost_is_owasp_strength():
    assert hash_password(PASSWORD).startswith("scrypt$17$8$1$")


@pytest.mark.parametrize("stored", [
    "", "plain-text-password", "scrypt$17$8$1$abc", "bcrypt$12$8$1$a$b",
    "scrypt$x$8$1$a$b", "scrypt$13$8$1$YWFh$YWFh",       # too weak to accept
    "scrypt$25$8$1$YWFh$YWFh",                            # too costly to even try
    "scrypt$14$8$2$YWFh$YWFh", "scrypt$14$8$1$!!!$YWFh",
])
def test_malformed_or_unreasonable_hashes_never_match(stored):
    start = time.monotonic()
    assert verify_password(PASSWORD, stored) is False
    assert time.monotonic() - start < 2  # a planted hash can't tie up the server


@pytest.mark.parametrize("password, ok", [
    ("short", False), ("a" * 129, False), ("aaaaaaaaaaaaaaaa", False),
    ("correct horse battery staple", True), ("Tr0ub4dor&3xyz", True),
])
def test_new_password_rules(password, ok):
    if ok:
        check_new_password(password)
    else:
        with pytest.raises(ValueError):
            check_new_password(password)


@pytest.mark.parametrize("raw, expected", [
    ("David", "david"), ("  dk207cr ", "dk207cr"), ("a.b-c_d", "a.b-c_d"),
    ("ab", None), ("x" * 33, None), ("bad name", None), ("<script>", None), (None, None),
    ("dávid", None),
])
def test_usernames(raw, expected):
    assert normalise_username(raw) == expected


# --- session cookies -------------------------------------------------------------------


def test_session_cookie_round_trip_and_tampering():
    now = [1_000_000.0]
    signer = SessionSigner(SECRET, clock=lambda: now[0])
    value = signer.make(user())
    assert signer.read(value) == ("david", 1)

    payload, signature = value.split(".")
    other = SessionSigner(SECRET, clock=lambda: now[0]).make(user("admin"))
    assert signer.read(other.split(".")[0] + "." + signature) is None   # swapped payload
    assert signer.read(payload + "." + signature[:-2] + "AA") is None    # forged signature
    assert SessionSigner("y" * 48, clock=lambda: now[0]).read(value) is None  # other secret
    for junk in (None, "", "abc", "a.b.c", "x" * 600, "." , "e30.e30"):
        assert signer.read(junk) is None

    now[0] += 31 * 86400
    assert signer.read(value) is None                                    # expired after 30 days


def test_cookie_issued_in_the_future_is_refused():
    future = SessionSigner(SECRET, clock=lambda: 2_000_000.0).make(user())
    assert SessionSigner(SECRET, clock=lambda: 1_000_000.0).read(future) is None


def test_short_secret_refused():
    with pytest.raises(ValueError):
        SessionSigner("too-short")


# --- lockout ----------------------------------------------------------------------------


def test_lockout_after_five_failures_then_expires():
    now = [0.0]
    guard = LoginGuard(clock=lambda: now[0])
    for _ in range(5):
        assert guard.locked_for("david") == 0
        guard.failed("david")
    assert 899 < guard.locked_for("david") <= 900
    assert guard.locked_for("someone-else") == 0
    now[0] += 901
    assert guard.locked_for("david") == 0
    guard.failed("david")
    guard.succeeded("david")
    assert guard.locked_for("david") == 0 and len(guard._recent("david", now[0])) == 0


# --- Auth ------------------------------------------------------------------------------


def make_auth(users, *, secure=False, clock=time.time):
    store = {u.username: u for u in users}
    return Auth(store.get, SECRET, secure_cookie=secure, clock=clock), store


def test_login_success_wrong_password_and_unknown_user_look_the_same():
    auth, _ = make_auth([user()])
    ok, wait = auth.login(" David ", PASSWORD)
    assert ok.username == "david" and wait == 0
    assert auth.login("david", "wrong password!") == (None, 0.0)
    assert auth.login("nobody", PASSWORD) == (None, 0.0)
    assert auth.login("bad name!", PASSWORD) == (None, 0.0)
    assert auth.login(123, PASSWORD) == (None, 0.0)
    assert auth.login("david", None) == (None, 0.0)


def test_disabled_users_cannot_log_in_or_keep_sessions():
    auth, store = make_auth([user()])
    cookie = auth.signer.make(store["david"])
    store["david"] = user(disabled=True)
    assert auth.login("david", PASSWORD) == (None, 0.0)   # login always reads fresh
    auth._cache.clear()                                    # (the real cache expires in 30 s)
    assert auth.session_user(cookie) is None


def test_lockout_applies_to_unknown_usernames_too():
    auth, _ = make_auth([user()])
    for _ in range(5):
        auth.login("ghost", "nope nope nope")
    user_, wait = auth.login("ghost", "nope nope nope")
    assert user_ is None and wait > 0  # same lockout either way: no username discovery


def test_locked_account_refuses_even_the_right_password():
    auth, _ = make_auth([user()])
    for _ in range(5):
        auth.login("david", "wrong password!")
    assert auth.login("david", PASSWORD)[1] > 0


def test_bumping_session_version_logs_out_everywhere():
    auth, store = make_auth([user()])
    cookie = auth.signer.make(store["david"])
    assert auth.session_user(cookie).username == "david"
    store["david"] = user(version=2)
    auth._cache.clear()  # (the real cache expires within 30 s)
    assert auth.session_user(cookie) is None


def test_deleted_user_session_is_refused():
    auth, store = make_auth([user()])
    cookie = auth.signer.make(store["david"])
    del store["david"]
    assert auth.session_user(cookie) is None


def test_cookie_attributes():
    local, _ = make_auth([user()])
    header = local.login_cookie(user())
    assert header.startswith("decklens_session=")
    for part in ("HttpOnly", "SameSite=Lax", "Path=/", f"Max-Age={30 * 86400}"):
        assert part in header
    assert "Secure" not in header and "Domain" not in header

    https, _ = make_auth([user()], secure=True)
    header = https.login_cookie(user())
    assert header.startswith("__Host-decklens_session=") and "Secure" in header
    assert "Max-Age=0" in https.logout_cookie()


# --- through the web app ----------------------------------------------------------------


class FakeClient:
    def get_player(self, tag):
        return {"tag": tag, "name": "Me", "currentDeck": []}

    def get_battle_log(self, tag):
        return []


@pytest.fixture
def site():
    auth, store = make_auth([user()])
    app = create_app(Services(client=FakeClient(), auth=auth))
    client = TestClient(app, raise_server_exceptions=False,
                        headers={"origin": "http://testserver"})
    return client, auth, store


def log_in(client, username="david", password=PASSWORD):
    return client.post("/api/login", json={"username": username, "password": password})


@pytest.mark.parametrize("method, path", [
    ("get", "/api/players/%232PP"), ("get", "/api/players/%232PP/decks"),
    ("get", "/api/players/%232PP/last-opponent"), ("get", "/api/search?name=dipo"),
    ("post", "/api/predict"), ("get", "/cards/26000000.png"), ("get", "/api/me"),
])
def test_everything_useful_needs_a_login(site, method, path):
    client, _, _ = site
    response = getattr(client, method)(path)
    assert response.status_code == 401 and response.json() == {"detail": "Please log in"}


def test_pages_redirect_to_login_and_back(site):
    client, _, _ = site
    home = client.get("/", follow_redirects=False)
    assert home.status_code == 303 and home.headers["location"] == "/login"
    page = client.get("/login")
    assert page.status_code == 200 and "login.js" in page.text
    assert client.get("/static/login.js").status_code == 200   # login page assets are public
    assert client.get("/api/health").status_code == 200

    assert log_in(client).status_code == 200
    assert client.get("/", follow_redirects=False).status_code == 200
    assert client.get("/login", follow_redirects=False).headers["location"] == "/"


def test_login_flow_and_logout(site):
    client, _, _ = site
    response = log_in(client, "David")
    assert response.status_code == 200 and response.json() == {"username": "david"}
    assert "HttpOnly" in response.headers["set-cookie"]
    assert client.get("/api/me").json() == {"username": "david", "login": True}
    assert client.get("/api/players/%232PP").status_code == 200

    out = client.post("/api/logout")
    assert out.status_code == 200 and "Max-Age=0" in out.headers["set-cookie"]
    assert client.get("/api/me").status_code == 401


def test_wrong_details_get_one_generic_answer_and_nothing_sensitive_is_logged(site, caplog):
    client, _, _ = site
    wrong = log_in(client, "david", "not the password")
    unknown = log_in(client, "nobody", "not the password")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json() == {"detail": "Wrong username or password"}
    assert "set-cookie" not in wrong.headers
    assert "not the password" not in caplog.text and "nobody" not in caplog.text
    assert "failed login" in caplog.text


def test_lockout_through_the_api(site):
    client, _, _ = site
    for _ in range(5):
        assert log_in(client, "david", "wrong password!").status_code == 401
    locked = log_in(client)
    assert locked.status_code == 429 and int(locked.headers["retry-after"]) > 800
    assert "Try again in 15 minutes" in locked.json()["detail"]


def test_login_requests_per_client_are_rate_limited(site):
    client, _, _ = site
    codes = [log_in(client, f"user{i}", "wrong password!").status_code for i in range(11)]
    assert codes[:10] == [401] * 10 and codes[10] == 429


@pytest.mark.parametrize("body", [
    {"username": "david"}, {"username": "david", "password": PASSWORD, "admin": True},
    {"username": "david", "password": "x" * 200}, {"username": "d" * 100, "password": PASSWORD},
])
def test_malformed_login_requests_rejected(site, body):
    client, _, _ = site
    assert client.post("/api/login", json=body).status_code == 422


def test_session_survives_until_password_change(site):
    client, auth, store = site
    log_in(client)
    store["david"] = user(version=2)   # what "users passwd" does
    auth._cache.clear()
    assert client.get("/api/me").status_code == 401


def test_cookie_forged_or_from_another_server_is_refused(site):
    client, _, _ = site
    forged = SessionSigner("someone-elses-secret-" + "z" * 30).make(user())
    client.cookies.set("decklens_session", forged)
    assert client.get("/api/me").status_code == 401


# --- cross-site request forgery --------------------------------------------------------


@pytest.mark.parametrize("headers", [
    {},                                                   # no Origin or Referer at all
    {"origin": "https://evil.example"},
    {"origin": "http://testserver.evil.example"},
    {"origin": "null"},                                   # sandboxed iframe / file://
    {"referer": "https://evil.example/page"},
])
def test_cross_site_posts_are_refused(headers):
    auth, _ = make_auth([user()])
    client = TestClient(create_app(Services(client=FakeClient(), auth=auth)),
                        raise_server_exceptions=False)
    response = client.post("/api/login", json={"username": "david", "password": PASSWORD},
                           headers=headers)
    assert response.status_code == 403
    assert response.json() == {"detail": "Cross-site request refused"}
    assert "content-security-policy" in response.headers


def test_same_site_referer_is_accepted_when_origin_is_missing():
    auth, _ = make_auth([user()])
    client = TestClient(create_app(Services(client=FakeClient(), auth=auth)))
    response = client.post("/api/login", json={"username": "david", "password": PASSWORD},
                           headers={"referer": "http://testserver/login"})
    assert response.status_code == 200


def test_get_requests_are_not_affected():
    client = TestClient(create_app(Services(client=FakeClient())))
    assert client.get("/api/health", headers={"origin": "https://evil.example"}).status_code == 200


# --- configuration ---------------------------------------------------------------------


def test_logins_are_on_by_default_and_need_a_real_secret(monkeypatch):
    from decklens.config import ConfigError
    from decklens.web.app import _auth_from_env

    monkeypatch.delenv("DECKLENS_LOGIN", raising=False)
    monkeypatch.delenv("DECKLENS_SECRET_KEY", raising=False)
    with pytest.raises(ConfigError, match="DECKLENS_SECRET_KEY"):
        _auth_from_env(lambda: None, https=False)
    monkeypatch.setenv("DECKLENS_SECRET_KEY", "replace-me" + "x" * 40)
    with pytest.raises(ConfigError):
        _auth_from_env(lambda: None, https=False)
    monkeypatch.setenv("DECKLENS_SECRET_KEY", SECRET)
    with pytest.raises(ConfigError, match="database"):
        _auth_from_env(None, https=False)
    assert _auth_from_env(lambda: None, https=True).cookie_name == "__Host-decklens_session"


def test_logins_can_only_be_switched_off_locally(monkeypatch):
    from decklens.config import ConfigError
    from decklens.web.app import _auth_from_env

    monkeypatch.setenv("DECKLENS_LOGIN", "off")
    assert _auth_from_env(None, https=False) is None
    with pytest.raises(ConfigError, match="only allowed for local use"):
        _auth_from_env(None, https=True)


# --- account tool ----------------------------------------------------------------------


def test_new_password_prompt_checks_rules_and_confirmation():
    from decklens.users import ask_new_password

    answers = iter(["correct horse battery staple", "correct horse battery staple"])
    assert ask_new_password(lambda _: next(answers)) == "correct horse battery staple"
    answers = iter(["correct horse battery staple", "correct horse battery stapel"])
    with pytest.raises(ValueError, match="didn't match"):
        ask_new_password(lambda _: next(answers))
    with pytest.raises(ValueError, match="characters"):
        ask_new_password(lambda _: "short")


def test_account_tool_rejects_bad_usernames(capsys):
    from decklens.users import main

    with pytest.raises(SystemExit):
        main(["add", "Bad Name!"])
    assert "usernames are 3-32 characters" in capsys.readouterr().err
