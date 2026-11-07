"""DeckLens as a desktop app: one double-click instead of PowerShell windows.

    scripts\\install-desktop.ps1        # once: adds DeckLens to the desktop and Start menu
    (or run:  .venv\\Scripts\\pythonw.exe -m decklens.desktop)

What opening it does:
1. A small window shows progress while it makes sure the database is up
   (starting Docker Desktop and the database container if needed).
2. Starts the website (on this computer only, 127.0.0.1) and the screen
   watcher inside the app, so there's no separate watcher login.
3. Opens DeckLens in its own app window (Edge's app mode, with its own
   profile, so you stay logged in between runs). Auto-scout is on by default.
4. Closing that window, or the Quit button, stops the website and watcher.
   Docker keeps running so the 3am crawl still works.

Logs go to logs\\desktop-YYYY-MM-DD.log (there's no console window).
"""

from __future__ import annotations

import json
import logging
import os
import queue
import subprocess  # nosec B404: only fixed commands (docker, the browser), never user input
import sys
import threading
import time
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("decklens.desktop")

ROOT = Path(__file__).resolve().parents[2]      # the DeckLens folder (editable install)
HOST, PORT = "127.0.0.1", 8000
URL = f"http://{HOST}:{PORT}"
PROFILE_DIR = Path.home() / ".decklens" / "app-window"
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW for helpers

DOCKER_DESKTOP = [Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
                  / "Docker" / "Docker" / "Docker Desktop.exe"]
APP_BROWSERS = [  # browsers with an "app window" mode, best first
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
    / "Microsoft" / "Edge" / "Application" / "msedge.exe",
    Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    / "Microsoft" / "Edge" / "Application" / "msedge.exe",
    Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    / "Google" / "Chrome" / "Application" / "chrome.exe",
    Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe",
]

Run = Callable[[list[str]], int]  # runs a command, returns its exit code


def run_quietly(command: list[str], timeout: float = 120) -> int:
    """Run a helper command with no console window popping up."""
    try:
        return subprocess.run(command, cwd=ROOT, timeout=timeout, creationflags=NO_WINDOW,  # nosec B603 B607
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
    except (OSError, subprocess.TimeoutExpired):
        return -1


def wait_for(check: Callable[[], bool], seconds: float, *, sleep: Callable[[float], None] = time.sleep,
             clock: Callable[[], float] = time.monotonic, every: float = 2.0) -> bool:
    deadline = clock() + seconds
    while True:
        if check():
            return True
        if clock() >= deadline:
            return False
        sleep(every)


def ensure_database(database_up: Callable[[], bool], status: Callable[[str], None], *,
                    run: Run = run_quietly, start_docker: Callable[[], bool] | None = None,
                    sleep: Callable[[float], None] = time.sleep,
                    clock: Callable[[], float] = time.monotonic) -> None:
    """Get the database answering, starting Docker Desktop and the container if needed."""
    if database_up():
        return
    if run(["docker", "info"]) != 0:
        status("Starting Docker Desktop… (this can take a minute)")
        if not (start_docker or start_docker_desktop)():
            raise RuntimeError("Docker Desktop isn't installed where expected. Start it yourself, "
                               "then open DeckLens again.")
        if not wait_for(lambda: run(["docker", "info"]) == 0, 180, sleep=sleep, clock=clock):
            raise RuntimeError("Docker Desktop didn't start. Open it yourself, then try again.")
    status("Starting the database…")
    if run(["docker", "compose", "up", "-d"]) != 0:
        raise RuntimeError("Couldn't start the database container (docker compose up failed).")
    if not wait_for(database_up, 60, sleep=sleep, clock=clock):
        raise RuntimeError("The database didn't come up. Check Docker Desktop.")


def start_docker_desktop(exists: Callable[[Path], bool] = Path.exists) -> bool:
    for exe in DOCKER_DESKTOP:
        if exists(exe):
            subprocess.Popen([str(exe)], creationflags=NO_WINDOW)  # nosec B603: fixed path
            return True
    return False


def database_checker() -> Callable[[], bool]:
    from .config import load_settings

    settings = load_settings(require_database=False)
    url = os.environ.get("WEB_DATABASE_URL") or settings.database_url

    def up() -> bool:
        import psycopg

        try:
            with psycopg.connect(url, connect_timeout=3):
                return True
        except psycopg.Error:
            return False
    return up


def decklens_running(fetch: Callable[[str], Any] | None = None) -> bool:
    """Is a DeckLens website already answering on this computer?"""
    def default(url: str) -> Any:
        with urllib.request.urlopen(url, timeout=2) as r:  # nosec B310: fixed local http URL
            return json.loads(r.read().decode())
    try:
        body = (fetch or default)(URL + "/api/health")
    except (OSError, ValueError):
        return False
    return isinstance(body, dict) and body.get("status") == "ok"


def find_app_browser(exists: Callable[[Path], bool] = Path.exists) -> Path | None:
    return next((b for b in APP_BROWSERS if exists(b)), None)


def app_window_command(browser: Path) -> list[str]:
    """A chromeless window with its own profile, so it stays logged in and is a
    separate process we can wait on (closing it closes DeckLens)."""
    return [str(browser), f"--app={URL}/", f"--user-data-dir={PROFILE_DIR}",
            "--no-first-run", "--no-default-browser-check", "--window-size=1150,1000"]


# --- the parts that run on the PC ---------------------------------------------------


def _log_to_file() -> None:
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    stream = open(logs / f"desktop-{date.today():%Y-%m-%d}.log", "a",  # noqa: SIM115
                  encoding="utf-8", buffering=1)
    # pythonw has no console: anything printed would otherwise crash or vanish.
    sys.stdout = sys.stderr = stream
    logging.basicConfig(level=logging.INFO, stream=stream,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


class Splash:
    """A small 'Starting DeckLens…' window (tkinter, part of Python)."""

    def __init__(self) -> None:
        import tkinter as tk

        self.root = tk.Tk()
        self.root.title("DeckLens")
        self.root.geometry("420x150")
        self.root.resizable(False, False)
        icon = ROOT / "src" / "decklens" / "web" / "static" / "decklens.ico"
        if icon.exists():
            try:
                self.root.iconbitmap(str(icon))
            except tk.TclError:
                log.debug("couldn't set the window icon")
        tk.Label(self.root, text="DeckLens", font=("Segoe UI", 16, "bold")).pack(pady=(18, 4))
        self.label = tk.Label(self.root, text="Starting…", font=("Segoe UI", 10), wraplength=380)
        self.label.pack()
        self.messages: queue.Queue = queue.Queue()
        self.root.after(100, self._pump)

    def _pump(self) -> None:
        while not self.messages.empty():
            kind, text = self.messages.get()
            if kind == "close":
                self.root.destroy()
                return
            self.label.config(text=text, fg="#b00020" if kind == "error" else "black")
        self.root.after(100, self._pump)

    def status(self, text: str) -> None:   # safe to call from any thread
        log.info(text)
        self.messages.put(("status", text))

    def error(self, text: str) -> None:
        log.error(text)
        self.messages.put(("error", text + "\n\n(Details: logs folder. Close this window.)"))

    def close(self) -> None:
        self.messages.put(("close", ""))


def run_watcher(board: Any, stop: threading.Event) -> None:
    """The screen watcher, inside the app: reports straight to the watch board."""
    from .web.watch import SCREEN_KEY

    def status(state: str, detail: str | None = None) -> None:
        board.set_status(SCREEN_KEY, state, detail)

    def keep_saying(state: str, detail: str | None) -> None:
        """A watcher that can't run keeps saying so (statuses go stale after 10 s)."""
        while True:
            status(state, detail)
            if stop.wait(5.0):
                return

    status("starting")
    try:
        from .watcher import Watcher, describe, read_config, text_reader, window_grabber
    except ImportError:
        log.warning("screen watcher not installed (pip install -e \".[watcher]\"): Auto-scout is off")
        return keep_saying("unavailable", "screen reader not installed")
    try:
        remembered = read_config().get("window")
        titles = [remembered] if isinstance(remembered, str) and remembered else []
        grab = window_grabber(titles, on_status=status)
        watcher = Watcher(grab, text_reader(),
                          lambda found: board.report(SCREEN_KEY, *found),
                          event=lambda kind: board.report(SCREEN_KEY, None, None, kind=kind),
                          rescan=lambda: board.take_rescan(SCREEN_KEY))
    except ImportError as exc:  # screen capture / text recognition packages missing
        log.warning("screen watcher not installed (%s): pip install -e \".[watcher]\"", exc.name)
        return keep_saying("unavailable", "screen reader not installed")
    except Exception:  # noqa: BLE001 - the website should keep working without it
        log.exception("screen watcher couldn't start")
        return keep_saying("unavailable", "screen watcher couldn't start (see the log)")
    log.info("screen watcher running")
    while not stop.wait(1.0):
        try:
            found = watcher.step()
            if found:
                log.info("opponent: %s", describe(found))
        except Exception:  # noqa: BLE001 - one bad look shouldn't end the watcher
            log.exception("screen watcher hiccup")


MY_BATTLES_EVERY = 15 * 60  # your battle log keeps about 25 games: often enough not to miss any


def collect_my_battles(stop: threading.Event, collect: Callable[[], Any] | None = None) -> None:
    """Store your own battles while the app is open, for your record."""
    from .collector import main as collector

    collect = collect or (lambda: collector(["--mine"]))
    while True:
        try:
            collect()
        except Exception:  # noqa: BLE001 - never take the app down; try again later
            log.exception("collecting your battles failed")
        if stop.wait(MY_BATTLES_EVERY):
            return


def main() -> int:
    os.chdir(ROOT)            # .env, models\ and logs\ live here
    _log_to_file()
    log.info("DeckLens desktop starting")

    browser = find_app_browser()
    if decklens_running():    # already open: just show another window
        log.info("DeckLens is already running; opening a window")
        _open_window(browser)
        return 0

    from .watcher import _dpi_aware
    _dpi_aware()
    splash = Splash()
    done = threading.Event()  # set when the app should close
    started: dict[str, Any] = {}

    def start_up() -> None:
        try:
            from .config import ConfigError

            splash.status("Checking the database…")
            ensure_database(database_checker(), splash.status)
            splash.status("Starting DeckLens…")
            import uvicorn

            from .web.app import create_app_from_env

            try:
                app = create_app_from_env()
            except ConfigError as exc:
                raise RuntimeError(str(exc)) from None
            app.state.services.quit_app = done.set
            server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=PORT, log_level="warning",
                                                   server_header=False))
            threading.Thread(target=server.run, name="website", daemon=True).start()
            if not wait_for(decklens_running, 20, every=0.5):
                raise RuntimeError(f"The website didn't start (is something else using port {PORT}?)")
            started["server"] = server
            stop_watcher = threading.Event()
            started["stop_watcher"] = stop_watcher
            threading.Thread(target=run_watcher, args=(app.state.services.watch, stop_watcher),
                             name="watcher", daemon=True).start()
            threading.Thread(target=collect_my_battles, args=(stop_watcher,),
                             name="my-battles", daemon=True).start()
            splash.close()
        except Exception as exc:  # noqa: BLE001 - show any start-up failure to the user
            log.exception("start-up failed")
            splash.error(str(exc) or type(exc).__name__)

    threading.Thread(target=start_up, name="start-up", daemon=True).start()
    splash.root.mainloop()    # returns when start-up closes it, or the user closes it
    if "server" not in started:
        return 1

    window = _open_window(browser)
    if window is not None:    # closing the app window closes DeckLens
        threading.Thread(target=watch_window, args=(window, done), daemon=True).start()
    done.wait()
    log.info("DeckLens closing")
    started["stop_watcher"].set()
    started["server"].should_exit = True
    time.sleep(1.0)
    return 0


def watch_window(window: Any, done: threading.Event, *,
                 clock: Callable[[], float] = time.monotonic, handoff_seconds: float = 5.0) -> None:
    """Close DeckLens when its window closes. If the browser process ends almost
    at once, it handed the window to an already-running browser, so we can't
    tell when it closes: stay up and let the page's Quit button close us."""
    opened = clock()
    window.wait()
    if clock() - opened < handoff_seconds:
        log.info("the app window was handed to a running browser: use Quit to close DeckLens")
        return
    done.set()


def _open_window(browser: Path | None) -> subprocess.Popen | None:
    if browser is None:       # no app-mode browser: a normal tab (use Quit to close)
        import webbrowser
        webbrowser.open(URL + "/")
        return None
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen(app_window_command(browser))  # nosec B603: fixed browser path and flags


if __name__ == "__main__":
    sys.exit(main())
