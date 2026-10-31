import threading
from pathlib import Path

import pytest

from decklens import desktop


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def runner(results):
    """Fake command runner: exit codes per command, in order; records calls."""
    calls = []

    def run(command):
        calls.append(" ".join(command))
        key = " ".join(command)
        codes = results.get(key, [0])
        return codes.pop(0) if len(codes) > 1 else codes[0]
    return run, calls


def test_database_already_up_does_nothing():
    run, calls = runner({})
    desktop.ensure_database(lambda: True, lambda s: None, run=run)
    assert calls == []


def test_starts_the_container_when_docker_is_running():
    clock, ups = Clock(), iter([False, False, True])
    run, calls = runner({})
    statuses = []
    desktop.ensure_database(lambda: next(ups), statuses.append, run=run,
                            sleep=clock.sleep, clock=clock)
    assert calls == ["docker info", "docker compose up -d"]
    assert statuses == ["Starting the database…"]


def test_starts_docker_desktop_first_when_needed():
    clock, ups = Clock(), iter([False, True])
    run, calls = runner({"docker info": [1, 1, 0]})        # down, still starting, ready
    started = []
    desktop.ensure_database(lambda: next(ups), lambda s: None, run=run,
                            start_docker=lambda: started.append(1) or True,
                            sleep=clock.sleep, clock=clock)
    assert started == [1] and calls.count("docker info") == 3 and calls[-1] == "docker compose up -d"


@pytest.mark.parametrize("results, start_docker, message", [
    ({"docker info": [1]}, lambda: False, "isn't installed"),
    ({"docker info": [1]}, lambda: True, "didn't start"),           # never becomes ready
    ({"docker compose up -d": [1]}, None, "docker compose up failed"),
    ({}, None, "didn't come up"),                                   # container up, db never answers
])
def test_clear_errors_when_something_wont_start(results, start_docker, message):
    clock = Clock()
    run, _ = runner(results)
    with pytest.raises(RuntimeError, match=message):
        desktop.ensure_database(lambda: False, lambda s: None, run=run, start_docker=start_docker,
                                sleep=clock.sleep, clock=clock)


def test_already_running_check():
    assert desktop.decklens_running(lambda url: {"status": "ok", "model": True})
    assert not desktop.decklens_running(lambda url: {"something": "else"})
    assert not desktop.decklens_running(lambda url: (_ for _ in ()).throw(OSError("refused")))


def test_app_window_prefers_edge_and_uses_its_own_profile():
    edge = desktop.APP_BROWSERS[0]
    assert desktop.find_app_browser(lambda p: p == edge) == edge
    assert desktop.find_app_browser(lambda p: False) is None
    cmd = desktop.app_window_command(Path("msedge.exe"))
    assert cmd[1] == "--app=http://127.0.0.1:8000/"
    assert any(a.startswith("--user-data-dir=") for a in cmd)
    assert desktop.URL.startswith("http://127.0.0.1:")          # never on the network


class FakeWindow:
    def __init__(self, clock, lifetime):
        self.clock, self.lifetime = clock, lifetime

    def wait(self):
        self.clock.now += self.lifetime


def test_closing_the_window_closes_the_app():
    clock, done = Clock(), threading.Event()
    desktop.watch_window(FakeWindow(clock, 600), done, clock=clock)
    assert done.is_set()


def test_window_handed_to_a_running_browser_keeps_the_app_open():
    clock, done = Clock(), threading.Event()
    desktop.watch_window(FakeWindow(clock, 0.5), done, clock=clock)
    assert not done.is_set()                                       # Quit button closes it instead


def test_missing_watcher_packages_leave_the_website_running(monkeypatch, caplog):
    import decklens.watcher as watcher_module

    def no_mss(titles):
        raise ModuleNotFoundError("No module named 'mss'", name="mss")

    monkeypatch.setattr(watcher_module, "window_grabber", no_mss)
    desktop.run_watcher(object(), threading.Event())       # returns instead of crashing
    assert "screen watcher not installed (mss)" in caplog.text
