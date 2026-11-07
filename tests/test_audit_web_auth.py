"""Reproductions for docs/audits/2026-11-07-web-auth.md, findings F1-F3.

Each pair is an attack test that fails while the defect is present and a legitimate-path
test that must keep passing after the fix.

The attack tests are marked xfail(strict=True) so CI stays green while the defect is open;
a fix makes them pass, strict xfail then fails the build until the mark is removed.
Run them as plain tests with: python -m pytest --runxfail <this file>
"""

import os
import threading
import time

import pytest
from fastapi.testclient import TestClient

import decklens.web.auth as auth_module
from decklens.web.app import Services, create_app
from decklens.web.auth import Auth

from test_auth import SECRET, make_auth, user
from test_web import FakeClient

# --- F1: no Host allowlist, so DNS rebinding reaches the local-only no-login mode -------

REBIND = "http://rebind.attacker.example:8000"


def _no_login_site(base_url):
    app = create_app(Services(client=FakeClient(), auth=None))  # DECKLENS_LOGIN=off
    return TestClient(app, base_url=base_url, headers={"origin": base_url},
                      raise_server_exceptions=False)


def test_rebound_hostname_cannot_use_the_no_login_api():
    site = _no_login_site(REBIND)
    assert site.get("/api/players/%232PP").status_code != 200
    assert site.post("/api/watch", json={"name": "fake", "clan": "fake"}).status_code != 200


@pytest.mark.parametrize("base_url", ["http://127.0.0.1:8000", "http://localhost:8000"])
def test_local_hostnames_still_work_without_logins(base_url):
    site = _no_login_site(base_url)
    assert site.get("/api/players/%232PP").status_code == 200


# --- F2: remove + add of the same username revives that username's old cookies ----------

URL = os.environ.get("DECKLENS_TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not URL, reason="DECKLENS_TEST_DATABASE_URL not set")


@pytest.fixture
def db():
    psycopg = pytest.importorskip("psycopg")
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute((root / "db" / "migrations" / "004_web_users.sql").read_text())
        c.execute("DELETE FROM web_users WHERE username = 'audit_friend'")
        yield c
        c.execute("DELETE FROM web_users WHERE username = 'audit_friend'")


def _auth_on(conn):
    from contextlib import contextmanager

    from decklens.web.auth import database_user_loader

    @contextmanager
    def connect():
        yield conn

    return Auth(database_user_loader(connect), SECRET, secure_cookie=False, user_cache_seconds=0)


@pytest.mark.xfail(strict=True, reason="2026-11-07-web-auth F2: open defect; remove this mark in the fix")
@needs_db
def test_removed_users_cookie_stays_dead_after_the_name_is_added_again(db):
    from decklens.users import add_user, remove_user

    add_user(db, "audit_friend", "first password, long enough")
    site = _auth_on(db)
    old_user, _ = site.login("audit_friend", "first password, long enough")
    old_cookie = site.signer.make(old_user)
    remove_user(db, "audit_friend")
    assert site.session_user(old_cookie) is None            # holds today

    add_user(db, "audit_friend", "a different password entirely")
    assert site.session_user(old_cookie) is None, "the removed account's cookie works again"


@needs_db
def test_a_freshly_added_user_can_log_in_and_keep_a_session(db):
    from decklens.users import add_user

    add_user(db, "audit_friend", "first password, long enough")
    site = _auth_on(db)
    found, _ = site.login("audit_friend", "first password, long enough")
    assert found is not None
    assert site.session_user(site.signer.make(found)) == found


# --- F3: login runs any number of 128 MB scrypt checks at the same time -----------------

def _count_concurrent_scrypt(monkeypatch):
    state = {"now": 0, "peak": 0}
    lock = threading.Lock()
    real = auth_module._scrypt

    def tracked(*args, **kwargs):
        with lock:
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
        try:
            time.sleep(0.3)  # long enough for every request to overlap
            return real(*args, **kwargs)
        finally:
            with lock:
                state["now"] -= 1

    monkeypatch.setattr(auth_module, "_scrypt", tracked)
    return state


def _parallel_logins(site, attempts):
    threads = [threading.Thread(target=site.login, args=(f"nobody{i}", "wrong password!"))
               for i in range(attempts)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


@pytest.mark.xfail(strict=True, reason="2026-11-07-web-auth F3: open defect; remove this mark in the fix")
def test_password_checks_do_not_all_run_at_once(monkeypatch):
    site, _ = make_auth([user()])
    state = _count_concurrent_scrypt(monkeypatch)
    _parallel_logins(site, 10)  # one client's whole per-minute login allowance
    assert state["peak"] <= 2, f"{state['peak']} scrypt checks (128 MB each) ran at once"


def test_one_login_at_a_time_still_works(monkeypatch):
    from test_auth import PASSWORD

    site, _ = make_auth([user()])
    _count_concurrent_scrypt(monkeypatch)
    found, wait = site.login("david", PASSWORD)
    assert found is not None and wait == 0
