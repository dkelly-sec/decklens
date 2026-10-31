"""Screen watcher: reads your opponent's name and clan off your mirrored phone
screen and sends them to the DeckLens page, which searches their decks.

    pip install -e ".[watcher]"              # once: screen capture + text recognition
    python -m decklens.watcher --calibrate   # once: drag a box around the phone screen
    python -m decklens.watcher --check       # see what it reads right now
    python -m decklens.watcher               # run it (Ctrl+C to stop)

Then tick "Auto-scout" on the DeckLens page.

How it stays on the right side of the game's rules: the game runs on your
phone as normal; this only reads pixels from the mirror window on your PC
(like a stream capture). It never sends input to the phone or game, never
touches game files, memory or network traffic, and never saves screenshots:
images live in memory for one read and only the recognised text is sent.

What it looks for (checked against real battle screenshots):
- top-right corner: the battle timer ("Time left: 2:54"), so menus and the
  loading screen are ignored;
- top-left corner: the opponent's name, with their clan on the line below.
"""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib.parse import urlsplit

from .names import fuzzy_score, search_key

log = logging.getLogger("decklens.watcher")

CONFIG_FILE = Path.home() / ".decklens" / "watcher.json"
# Regions as fractions of the phone screen: (left, top, right, bottom).
TOP_LEFT = (0.0, 0.0, 0.62, 0.10)     # opponent name and clan
TOP_RIGHT = (0.78, 0.02, 1.0, 0.10)   # "Time left: 2:54" / overtime
BATTLE_TEXT = re.compile(r"\d\s*:\s*\d\d|left|overtime", re.IGNORECASE)
MIN_CONFIDENCE = 0.5
SAME_READ = 0.8  # fuzzy score at which two reads count as the same player


@dataclass(frozen=True)
class TextLine:
    text: str
    x: float  # left edge, as a fraction of the region's width
    y: float  # top edge, as a fraction of the region's height
    confidence: float


def lines_from_ocr(result: Any, width: int, height: int) -> list[TextLine]:
    """RapidOCR output [[box, text, confidence], ...] -> TextLines. Junk is skipped."""
    lines = []
    for item in result or []:
        try:
            box, text, confidence = item[0], str(item[1]), float(item[2])
            x = min(float(p[0]) for p in box) / max(width, 1)
            y = min(float(p[1]) for p in box) / max(height, 1)
        except (TypeError, ValueError, IndexError):
            continue
        if text.strip():
            lines.append(TextLine(text.strip(), x, y, confidence))
    return lines


def in_battle(top_right: Sequence[TextLine]) -> bool:
    return any(BATTLE_TEXT.search(line.text) for line in top_right)


def read_opponent(top_left: Sequence[TextLine]) -> tuple[str, str | None] | None:
    """(name, clan or None) from the top-left corner of the battle screen."""
    lines = sorted((ln for ln in top_left
                    if ln.x < 0.35 and ln.confidence >= MIN_CONFIDENCE
                    and len(search_key(ln.text)) >= 1 and not ln.text.strip().isdigit()),
                   key=lambda ln: ln.y)
    if not lines:
        return None
    name = lines[0]
    clan = next((ln for ln in lines[1:] if 0.1 < ln.y - name.y < 0.5), None)
    return name.text[:40], (clan.text[:50] if clan else None)


def _same(a: tuple[str, str | None], b: tuple[str, str | None]) -> bool:
    def close(x: str | None, y: str | None) -> bool:
        if not x or not y:
            return x == y
        kx, ky = search_key(x), search_key(y)
        return fuzzy_score(kx, ky) >= SAME_READ and fuzzy_score(ky, kx) >= SAME_READ
    return close(a[0], b[0]) and close(a[1], b[1])


def crop(image: Any, region: tuple[float, float, float, float]) -> Any:
    h, w = image.shape[:2]
    left, top, right, bottom = region
    return image[int(top * h):int(bottom * h), int(left * w):int(right * w)]


class Watcher:
    """Reports each opponent once per battle, after two reads agree."""

    def __init__(self, grab: Callable[[], Any], ocr: Callable[[Any], Any],
                 send: Callable[[str, str | None], None], *,
                 confirm: int = 2, rearm_after: int = 3):
        self.grab, self.ocr, self.send = grab, ocr, send
        self.confirm, self.rearm_after = confirm, rearm_after
        self.reported = False
        self.pending: tuple[str, str | None] | None = None
        self.agree = 0
        self.misses = 0

    def _read(self, image: Any, region: tuple[float, float, float, float]) -> list[TextLine]:
        part = crop(image, region)
        h, w = part.shape[:2]
        return lines_from_ocr(self.ocr(part), w, h)

    def step(self) -> tuple[str, str | None] | None:
        """One look at the screen. Returns the opponent if they were just reported."""
        image = self.grab()
        if not in_battle(self._read(image, TOP_RIGHT)):
            self.misses += 1
            if self.misses >= self.rearm_after:  # battle over: ready for the next one
                self.reported, self.pending, self.agree = False, None, 0
            return None
        self.misses = 0
        if self.reported:
            return None
        found = read_opponent(self._read(image, TOP_LEFT))
        if found is None:
            return None
        if self.pending is not None and _same(found, self.pending):
            self.agree += 1
        else:
            self.pending, self.agree = found, 1
        if self.agree >= self.confirm:
            self.send(*found)
            self.reported = True
            return found
        return None


# --- talking to DeckLens ---------------------------------------------------------


class NeedLogin(Exception):
    pass


class DeckLensClient:
    """Logs in like the page does and posts what the watcher read (text only)."""

    def __init__(self, base_url: str, session: Any = None):
        parts = urlsplit(base_url)
        local = parts.hostname in ("127.0.0.1", "localhost", "::1")
        if parts.scheme not in ("http", "https") or (parts.scheme == "http" and not local):
            raise ValueError("use https:// for a DeckLens server that isn't on this computer")
        self.base = f"{parts.scheme}://{parts.netloc}"
        if session is None:
            import requests
            session = requests.Session()
        self.session = session
        # The server's cross-site check wants requests to come from its own origin.
        self.session.headers.update({"Origin": self.base})

    def login(self, username: str, password: str) -> None:
        r = self.session.post(self.base + "/api/login", timeout=10,
                              json={"username": username, "password": password})
        if r.status_code != 200:
            detail = r.json().get("detail") if r.headers.get("content-type", "").startswith(
                "application/json") else None
            raise NeedLogin(detail or f"login failed (HTTP {r.status_code})")

    def report(self, name: str, clan: str | None) -> None:
        r = self.session.post(self.base + "/api/watch", timeout=10,
                              json={"name": name, "clan": clan})
        if r.status_code == 401:
            raise NeedLogin("session expired")
        r.raise_for_status()


# --- screen capture, text recognition, calibration (Windows PC) ------------------


def _dpi_aware() -> None:
    """Use real pixels on scaled Windows displays, so boxes line up."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError) as exc:  # older Windows: boxes may be off on scaled screens
            log.debug("couldn't enable DPI awareness: %s", exc)


def load_region() -> dict[str, int]:
    try:
        region = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        if all(isinstance(region.get(k), int) for k in ("left", "top", "width", "height")):
            return region
    except (OSError, ValueError, AttributeError):
        pass
    raise SystemExit("No phone screen set yet: run  python -m decklens.watcher --calibrate")


def screen_grabber(region: dict[str, int]) -> Callable[[], Any]:
    import mss
    import numpy as np

    sct = mss.mss()

    def grab() -> Any:
        shot = np.asarray(sct.grab(region))     # BGRA, in memory only
        return shot[:, :, 2::-1].copy()         # -> RGB
    return grab


def text_reader() -> Callable[[Any], Any]:
    from rapidocr_onnxruntime import RapidOCR

    engine = RapidOCR()
    return lambda image: engine(image)[0]


def calibrate() -> None:
    """Drag a box around the phone screen in the Phone Link window."""
    import tkinter as tk

    picked: dict[str, int] = {}
    root = tk.Tk()
    root.attributes("-fullscreen", True)
    root.attributes("-alpha", 0.3)
    root.attributes("-topmost", True)
    canvas = tk.Canvas(root, cursor="cross", bg="black", highlightthickness=0)
    canvas.pack(fill="both", expand=True)
    canvas.create_text(root.winfo_screenwidth() // 2, 40, fill="white", font=("Segoe UI", 18),
                       text="Drag a box around the phone screen (just the game, no window "
                            "borders). Esc to cancel.")
    start: dict[str, int] = {}
    rect = [None]

    def down(e: Any) -> None:
        start.update(x=e.x_root, y=e.y_root)
        rect[0] = canvas.create_rectangle(e.x, e.y, e.x, e.y, outline="red", width=3)

    def drag(e: Any) -> None:
        if rect[0] is not None:
            canvas.coords(rect[0], start["x"] - root.winfo_rootx(), start["y"] - root.winfo_rooty(),
                          e.x, e.y)

    def up(e: Any) -> None:
        left, top = min(start["x"], e.x_root), min(start["y"], e.y_root)
        width, height = abs(e.x_root - start["x"]), abs(e.y_root - start["y"])
        if width > 50 and height > 100:
            picked.update(left=left, top=top, width=width, height=height)
        root.destroy()

    canvas.bind("<ButtonPress-1>", down)
    canvas.bind("<B1-Motion>", drag)
    canvas.bind("<ButtonRelease-1>", up)
    root.bind("<Escape>", lambda e: root.destroy())
    root.mainloop()
    if not picked:
        raise SystemExit("Cancelled: nothing saved.")
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(picked), encoding="utf-8")
    ratio = picked["height"] / picked["width"]
    print(f"Saved the phone screen: {picked['width']}x{picked['height']} at "
          f"({picked['left']}, {picked['top']}).")
    if not 1.8 <= ratio <= 2.4:
        print("That box isn't phone-shaped (expected about 2:1 tall). If reads fail, "
              "calibrate again around just the game picture.")


def check() -> None:
    """One read of the screen, to confirm calibration."""
    grab, ocr = screen_grabber(load_region()), text_reader()
    image = grab()
    top_right = lines_from_ocr(ocr(crop(image, TOP_RIGHT)), *crop(image, TOP_RIGHT).shape[1::-1])
    top_left = lines_from_ocr(ocr(crop(image, TOP_LEFT)), *crop(image, TOP_LEFT).shape[1::-1])
    print("Top right:", [ln.text for ln in top_right])
    print("Top left: ", [ln.text for ln in top_left])
    print("In a battle:", "yes" if in_battle(top_right) else "no (start a match and check again)")
    found = read_opponent(top_left)
    if found:
        print(f"Opponent: {found[0]}" + (f"  clan: {found[1]}" if found[1] else "  (no clan)"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read your opponent off your mirrored phone.")
    parser.add_argument("--calibrate", action="store_true", help="set the phone screen area")
    parser.add_argument("--check", action="store_true", help="read the screen once and show it")
    parser.add_argument("--url", default=os.environ.get("DECKLENS_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--user", default=os.environ.get("DECKLENS_USER"))
    parser.add_argument("--interval", type=float, default=1.0, help="seconds between looks")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    _dpi_aware()

    if args.calibrate:
        calibrate()
        return 0
    if args.check:
        check()
        return 0
    if not 0.3 <= args.interval <= 10:
        parser.error("--interval must be 0.3-10 seconds")

    client = DeckLensClient(args.url)
    username = args.user or input("DeckLens username: ").strip()

    def log_in() -> None:
        while True:
            try:
                client.login(username, getpass.getpass("DeckLens password: "))
                return
            except NeedLogin as exc:
                print(f"Couldn't log in: {exc}")

    log_in()

    def send(name: str, clan: str | None) -> None:
        try:
            client.report(name, clan)
        except NeedLogin:
            print("DeckLens logged you out; log in again.")
            log_in()
            client.report(name, clan)
        log.info("opponent: %s%s -> sent to DeckLens", name, f" ({clan})" if clan else "")

    watcher = Watcher(screen_grabber(load_region()), text_reader(), send)
    log.info("watching your phone screen (Ctrl+C to stop). Tick Auto-scout on the DeckLens page.")
    try:
        while True:
            started = time.monotonic()
            try:
                watcher.step()
            except OSError as exc:  # network blip talking to DeckLens: keep watching
                log.warning("couldn't reach DeckLens: %s", type(exc).__name__)
            time.sleep(max(0.0, args.interval - (time.monotonic() - started)))
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
